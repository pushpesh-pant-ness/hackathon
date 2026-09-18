"""FastAPI backend: POST /ingest, GET /ingest/{job_id}, POST /chat, GET /sessions/{id},
GET /analytics/report. See architecture.md Section 9 for the full API contract.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import analytics, db, flows, retrieval, sessions
from ingest import pipeline as ingest_pipeline

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Dynamic Website RAG Chatbot", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IngestRequest(BaseModel):
    url: str = Field(min_length=1)


class IngestResponse(BaseModel):
    session_id: str
    job_id: str
    status: str


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1)


class Source(BaseModel):
    title: str
    url: str
    chunk_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    flow: str
    sources: list[Source]


def _run_ingestion_job(session_id: str, job_id: str, url: str) -> None:
    db.update_crawl_job(job_id, status="running", started_at=_now_iso())
    try:
        result = ingest_pipeline.run_ingestion(session_id, url, sessions.session_dir(session_id))
    except ingest_pipeline.IngestionNotImplemented as exc:
        logger.warning("ingest.pipeline not implemented yet, seeding %s from fixture_demo: %s", session_id, exc)
        try:
            manifest = sessions.seed_from_fixture(session_id)
        except FileNotFoundError as fixture_exc:
            db.update_crawl_job(job_id, status="failed", error=str(fixture_exc), finished_at=_now_iso())
            sessions.update_session(session_id, status="failed")
            return
        db.update_crawl_job(
            job_id,
            status="done",
            finished_at=_now_iso(),
            pages_discovered=manifest["pages_discovered"],
            pages_retained=manifest["pages_retained"],
            pages_failed=manifest.get("pages_failed", 0),
        )
        retrieval.invalidate_cache(session_id)
        return
    except Exception as exc:  # crawl failures must not crash the worker (FINAL_PLAN Sec. 28)
        logger.exception("Ingestion failed for session %s", session_id)
        db.update_crawl_job(job_id, status="failed", error=str(exc), finished_at=_now_iso())
        sessions.update_session(session_id, status="failed")
        return

    db.update_crawl_job(
        job_id,
        status="done",
        finished_at=_now_iso(),
        pages_discovered=result.get("pages_discovered", 0),
        pages_retained=result.get("pages_retained", 0),
        pages_failed=result.get("pages_failed", 0),
    )
    sessions.update_session(
        session_id,
        status="ready",
        pages_discovered=result.get("pages_discovered", 0),
        pages_retained=result.get("pages_retained", 0),
        chunks=result.get("chunks", 0),
    )
    retrieval.invalidate_cache(session_id)


@app.post("/ingest", response_model=IngestResponse)
def create_ingestion(payload: IngestRequest, background_tasks: BackgroundTasks) -> IngestResponse:
    session_id = sessions.create_session(payload.url)
    job_id = db.create_crawl_job(session_id)
    background_tasks.add_task(_run_ingestion_job, session_id, job_id, payload.url)
    return IngestResponse(session_id=session_id, job_id=job_id, status="queued")


@app.get("/ingest/{job_id}")
def get_ingestion_status(job_id: str) -> dict:
    job = db.get_crawl_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    session = sessions.get_full_session(job["session_id"])
    return {
        "session_id": job["session_id"],
        "job_id": job["id"],
        "status": job["status"],
        "pages_discovered": job["pages_discovered"],
        "pages_retained": job["pages_retained"],
        "pages_failed": job["pages_failed"],
        "chunks": session["chunks"] if session else 0,
        "services": session["services"] if session else [],
        "error": job["error"],
    }


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    if not sessions.session_exists(payload.session_id):
        raise HTTPException(status_code=404, detail="session not found")
    db.insert_message(payload.session_id, role="user", content=payload.message)
    result = flows.route(payload.session_id, payload.message)
    return ChatResponse(**result)


@app.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    session = sessions.get_full_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.get("/analytics/report")
def get_analytics_report(session_id: str) -> dict:
    try:
        return analytics.report(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

"""FastAPI backend: POST /ingest, GET /ingest/{job_id}, POST /ingest/{job_id}/build,
POST /chat, GET /sessions/{id}, GET /analytics/report. See architecture.md Section 9
for the full API contract.
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
from ingest.crawler import crawl

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


def _mark_failed(job_id: str, session_id: str, error: str, **job_fields) -> None:
    db.update_crawl_job(job_id, status="failed", error=error, finished_at=_now_iso(), **job_fields)
    sessions.update_session(session_id, status="failed")


def _run_crawl_job(session_id: str, job_id: str, url: str) -> None:
    """Phase 1: discover the site map. Crawls the site only — no embedding/index
    build yet, so the user can see what was found before paying for that step.
    """
    db.update_crawl_job(job_id, status="crawling", started_at=_now_iso())
    try:
        crawl_result = crawl(url)
    except Exception as exc:  # crawl failures must not crash the worker (FINAL_PLAN Sec. 28)
        logger.exception("Crawl failed for session %s", session_id)
        _mark_failed(job_id, session_id, str(exc))
        return

    if not crawl_result.pages:
        _mark_failed(
            job_id,
            session_id,
            "No usable pages were found on this site.",
            pages_discovered=crawl_result.discovered,
            pages_failed=crawl_result.pages_failed,
        )
        return

    try:
        ingest_pipeline.save_sitemap(session_id, crawl_result)
    except Exception as exc:
        logger.exception("Saving site map failed for session %s", session_id)
        _mark_failed(
            job_id,
            session_id,
            str(exc),
            pages_discovered=crawl_result.discovered,
            pages_retained=crawl_result.pages_retained,
            pages_failed=crawl_result.pages_failed,
        )
        return

    db.update_crawl_job(
        job_id,
        status="sitemap_ready",
        pages_discovered=crawl_result.discovered,
        pages_retained=crawl_result.pages_retained,
        pages_failed=crawl_result.pages_failed,
    )
    sessions.update_session(
        session_id,
        status="crawled",
        pages_discovered=crawl_result.discovered,
        pages_retained=crawl_result.pages_retained,
    )


def _run_build_job(session_id: str, job_id: str) -> None:
    """Phase 2: turn the saved site map into a queryable knowledge base."""
    db.update_crawl_job(job_id, status="building")
    try:
        result = ingest_pipeline.build_from_sitemap(session_id)
        if result.status != "ready":
            # Zero usable chunks (§28) - never mark the session ready with no index.
            raise RuntimeError(result.error or "Knowledge base build produced no usable chunks.")
        for doc in result.documents:
            db.insert_document(
                session_id,
                url=doc["url"],
                title=doc["title"],
                service=doc["service"],
                document_id=doc["document_id"],
            )
    except Exception as exc:  # build failures must not crash the worker (FINAL_PLAN Sec. 28)
        logger.exception("Knowledge base build failed for session %s", session_id)
        _mark_failed(job_id, session_id, str(exc))
        return

    db.update_crawl_job(job_id, status="done", finished_at=_now_iso())
    sessions.update_session(session_id, status="ready", chunks=result.chunks)
    retrieval.invalidate_cache(session_id)


@app.post("/ingest", response_model=IngestResponse)
def create_ingestion(payload: IngestRequest, background_tasks: BackgroundTasks) -> IngestResponse:
    session_id = sessions.create_session(payload.url)
    job_id = db.create_crawl_job(session_id)
    background_tasks.add_task(_run_crawl_job, session_id, job_id, payload.url)
    return IngestResponse(session_id=session_id, job_id=job_id, status="queued")


@app.post("/ingest/{job_id}/build")
def build_knowledge_base(job_id: str, background_tasks: BackgroundTasks) -> dict:
    job = db.get_crawl_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] != "sitemap_ready":
        raise HTTPException(
            status_code=409, detail=f"job is '{job['status']}', expected 'sitemap_ready'"
        )
    background_tasks.add_task(_run_build_job, job["session_id"], job_id)
    return {"session_id": job["session_id"], "job_id": job_id, "status": "building"}


@app.get("/ingest/{job_id}")
def get_ingestion_status(job_id: str) -> dict:
    job = db.get_crawl_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    session = sessions.get_full_session(job["session_id"])
    try:
        sitemap = ingest_pipeline.load_sitemap(job["session_id"])
    except FileNotFoundError:
        sitemap = None
    return {
        "session_id": job["session_id"],
        "job_id": job["id"],
        "status": job["status"],
        "pages_discovered": job["pages_discovered"],
        "pages_retained": job["pages_retained"],
        "pages_failed": job["pages_failed"],
        "chunks": session["chunks"] if session else 0,
        "services": session["services"] if session else [],
        "sitemap": sitemap,
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

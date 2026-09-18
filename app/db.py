"""SQLite persistence layer: sessions, crawl_jobs, documents, messages, flow_events.

Schema matches architecture.md Section 8 / FINAL_PLAN_IMPROVED.md Section 26.
Opens a fresh connection per call rather than pooling - simple and safe enough for a
hackathon-scale SQLite file shared by the FastAPI process and any one-off scripts.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    website_url TEXT NOT NULL,
    normalized_host TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    pages_discovered INTEGER DEFAULT 0,
    pages_retained INTEGER DEFAULT 0,
    chunks INTEGER DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS crawl_jobs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    status TEXT NOT NULL DEFAULT 'queued',
    started_at TEXT,
    finished_at TEXT,
    pages_discovered INTEGER DEFAULT 0,
    pages_retained INTEGER DEFAULT 0,
    pages_failed INTEGER DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    url TEXT NOT NULL,
    title TEXT,
    service TEXT,
    status TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    flow TEXT,
    sources_json TEXT,
    retrieval_score REAL,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flow_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    flow TEXT NOT NULL,
    confidence REAL,
    matched TEXT,
    answered INTEGER,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.sqlite_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)


# --- sessions -----------------------------------------------------------------

def create_session(website_url: str, normalized_host: str | None = None, session_id: str | None = None) -> str:
    sid = session_id or _new_id()
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO sessions (id, website_url, normalized_host, status, created_at, updated_at)
               VALUES (?, ?, ?, 'pending', ?, ?)
               ON CONFLICT(id) DO UPDATE SET website_url=excluded.website_url, updated_at=excluded.updated_at""",
            (sid, website_url, normalized_host, now, now),
        )
    return sid


def get_session(session_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["meta"] = json.loads(result.get("meta") or "{}")
    return result


def update_session(session_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    columns = ", ".join(f"{key} = ?" for key in fields)
    with get_connection() as conn:
        conn.execute(f"UPDATE sessions SET {columns} WHERE id = ?", (*fields.values(), session_id))


def get_session_meta(session_id: str) -> dict[str, Any]:
    session = get_session(session_id)
    return session["meta"] if session else {}


def update_session_meta(session_id: str, **patch: Any) -> dict[str, Any]:
    """Shallow-merge `patch` into the session's meta JSON blob (conversation state lives here)."""
    meta = get_session_meta(session_id)
    meta.update(patch)
    update_session(session_id, meta=json.dumps(meta))
    return meta


# --- crawl_jobs -----------------------------------------------------------------

def create_crawl_job(session_id: str, job_id: str | None = None) -> str:
    jid = job_id or _new_id()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO crawl_jobs (id, session_id, status, started_at) VALUES (?, ?, 'queued', ?)",
            (jid, session_id, _now()),
        )
    return jid


def update_crawl_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    with get_connection() as conn:
        conn.execute(f"UPDATE crawl_jobs SET {columns} WHERE id = ?", (*fields.values(), job_id))


def get_crawl_job(job_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


# --- documents --------------------------------------------------------------

def insert_document(
    session_id: str,
    url: str,
    title: str | None = None,
    service: str | None = None,
    status: str = "retained",
    document_id: str | None = None,
) -> str:
    did = document_id or _new_id()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (id, session_id, url, title, service, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (did, session_id, url, title, service, status, _now()),
        )
    return did


def list_documents(session_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM documents WHERE session_id = ?", (session_id,)).fetchall()
    return [dict(r) for r in rows]


def list_services(session_id: str) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT service FROM documents WHERE session_id = ? AND service IS NOT NULL ORDER BY service",
            (session_id,),
        ).fetchall()
    return [r["service"] for r in rows]


# --- messages -----------------------------------------------------------------

def insert_message(
    session_id: str,
    role: str,
    content: str,
    flow: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    retrieval_score: float | None = None,
    latency_ms: int | None = None,
) -> str:
    mid = _new_id()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO messages
               (id, session_id, role, content, flow, sources_json, retrieval_score, latency_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mid,
                session_id,
                role,
                content,
                flow,
                json.dumps(sources or []),
                retrieval_score,
                latency_ms,
                _now(),
            ),
        )
    return mid


def list_messages(session_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC", (session_id,)
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["sources"] = json.loads(item.pop("sources_json") or "[]")
        result.append(item)
    return result


# --- flow_events --------------------------------------------------------------

def insert_flow_event(
    session_id: str,
    flow: str,
    confidence: float | None = None,
    matched: str | None = None,
    answered: bool = False,
) -> str:
    eid = _new_id()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO flow_events (id, session_id, flow, confidence, matched, answered, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (eid, session_id, flow, confidence, matched, int(answered), _now()),
        )
    return eid


def list_flow_events(session_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM flow_events WHERE session_id = ? ORDER BY created_at ASC", (session_id,)
        ).fetchall()
    return [dict(r) for r in rows]

"""Session lifecycle + lightweight conversation state (current_service/last_flow/last_sources).

Session state is persisted in the sessions.meta JSON blob (via app.db) rather than kept
in memory, so it survives FastAPI restarts and UI refreshes (see FINAL_PLAN_IMPROVED.md
Section 25 / Section 17).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app import db
from app.config import settings

DEFAULT_STATE = {"current_service": None, "last_flow": None, "last_sources": []}


def normalize_host(url: str) -> str | None:
    hostname = urlparse(url).hostname
    return hostname.lower() if hostname else None


def create_session(website_url: str, session_id: str | None = None) -> str:
    host = normalize_host(website_url)
    sid = db.create_session(website_url, normalized_host=host, session_id=session_id)
    settings.session_dir(sid).mkdir(parents=True, exist_ok=True)
    return sid


def session_dir(session_id: str) -> Path:
    return settings.session_dir(session_id)


def session_exists(session_id: str) -> bool:
    return db.get_session(session_id) is not None


def get_session_state(session_id: str) -> dict[str, Any]:
    meta = db.get_session_meta(session_id)
    state = dict(DEFAULT_STATE)
    state.update({k: v for k, v in meta.items() if k in DEFAULT_STATE})
    return state


def update_session_state(session_id: str, **patch: Any) -> dict[str, Any]:
    unknown = set(patch) - set(DEFAULT_STATE)
    if unknown:
        raise ValueError(f"Unknown session state field(s): {unknown}")
    db.update_session_meta(session_id, **patch)
    return get_session_state(session_id)


def get_full_session(session_id: str) -> dict[str, Any] | None:
    session = db.get_session(session_id)
    if session is None:
        return None
    return {
        **session,
        "state": get_session_state(session_id),
        "services": db.list_services(session_id),
        "messages": db.list_messages(session_id),
        "flow_events": db.list_flow_events(session_id),
    }


def seed_from_fixture(session_id: str, fixture_name: str = "fixture_demo") -> dict[str, Any]:
    """Copy the prebuilt fixture vector index into a real session directory.

    Used as the Phase 0-3 stand-in for Dev A's real crawl pipeline (see TEAM_PLAN.md
    "Key Trick") so /ingest -> /chat works end-to-end before the real crawler exists.
    Auto-builds data/sessions/fixture_demo on first use so there's no manual pre-step.
    """
    src = settings.sessions_dir / fixture_name
    if not src.exists() and fixture_name == "fixture_demo":
        from scripts.build_fixture_session import build as _build_fixture

        _build_fixture()
    if not src.exists():
        raise FileNotFoundError(
            f"Fixture session '{fixture_name}' not found at {src}. Run scripts/build_fixture_session.py first."
        )
    dst = session_dir(session_id)
    dst.mkdir(parents=True, exist_ok=True)
    for filename in ("index.faiss", "chunks.json", "manifest.json"):
        shutil.copy(src / filename, dst / filename)

    manifest = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    for doc in manifest.get("documents", []):
        db.insert_document(
            session_id,
            url=doc["source_url"],
            title=doc.get("title"),
            service=doc.get("service"),
        )
    update_session(
        session_id,
        status="ready",
        pages_discovered=manifest.get("pages_discovered", 0),
        pages_retained=manifest.get("pages_retained", 0),
        chunks=manifest.get("chunks", 0),
    )
    return manifest


def update_session(session_id: str, **fields: Any) -> None:
    db.update_session(session_id, **fields)

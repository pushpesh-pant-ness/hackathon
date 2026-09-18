"""Tests for the ingestion pipeline assembly + reload (Dev A — Milestone 4).

Network-free: synthetic crawled pages + deterministic embeddings written to a
temp session dir, then reloaded to prove restart-persistence and retrieval.
"""

from __future__ import annotations

import pytest

from app.bedrock import local_hash_embed
from app.config import settings
from app.vectorstore import SessionIndex
from ingest.crawler import CrawlResult, RetainedPage
from ingest.pipeline import process_pages

SEED = "https://acme.com"


def _embed(texts: list[str]) -> list[list[float]]:
    return [local_hash_embed(t, settings.embedding_dimensions) for t in texts]


def _page(path: str, title: str, body_heading: str, body: str) -> RetainedPage:
    url = f"{SEED}{path}"
    html = (
        f"<html><head><title>{title}</title></head><body><main>"
        f"<h1>{title}</h1><p>Intro paragraph about {title}.</p>"
        f"<h2>{body_heading}</h2><p>{body}</p>"
        f"</main></body></html>"
    )
    return RetainedPage(url, url, 200, "text/html", html, depth=1)


def _crawl_result(pages: list[RetainedPage], failed: int = 0) -> CrawlResult:
    return CrawlResult(
        seed_url=SEED, host="acme.com", pages=pages,
        failures=[], discovered=len(pages) + failed,
    )


@pytest.fixture
def _tmp_data(monkeypatch, tmp_path):
    # settings is a frozen dataclass, so patch the method on the class (all writers use it).
    monkeypatch.setattr(type(settings), "session_dir", lambda self, sid: tmp_path / sid)
    return tmp_path


def test_pipeline_builds_ready_session_and_persists(_tmp_data):
    pages = [
        _page("/services/salesforce", "Salesforce", "Benefits", "Faster onboarding and clean data."),
        _page("/services/data-engineering", "Data Engineering", "Use Cases", "Redshift and Glue pipelines."),
        _page("/contact", "Contact", "Reach Us", "Email hello at acme dot com anytime today."),
    ]
    result = process_pages("sess1", SEED, _crawl_result(pages), embed_fn=_embed,
                           embedding_model="deterministic-test")

    assert result.status == "ready"
    assert result.pages_retained == 3
    assert result.chunks > 0
    assert result.services == ["Data Engineering", "Salesforce"]

    session_dir = _tmp_data / "sess1"
    assert (session_dir / "index.faiss").exists()
    assert (session_dir / "chunks.json").exists()
    assert (session_dir / "manifest.json").exists()
    assert list((session_dir / "raw").glob("*.html"))
    assert list((session_dir / "cleaned").glob("*.txt"))


def test_pipeline_reload_after_restart_retrieves(_tmp_data):
    pages = [
        _page("/services/salesforce", "Salesforce", "Benefits", "Faster onboarding and clean data."),
        _page("/services/data-engineering", "Data Engineering", "Use Cases", "Redshift and Glue pipelines."),
    ]
    process_pages("sess2", SEED, _crawl_result(pages), embed_fn=_embed,
                  embedding_model="deterministic-test")

    # Fresh load simulates a process restart.
    loaded = SessionIndex.load("sess2")
    assert loaded.manifest["status"] == "ready"
    assert loaded.manifest["embedding_dimensions"] == settings.embedding_dimensions

    target = loaded.chunks[0]
    hits = loaded.search(_embed([target["text"]])[0], k=3)
    assert hits[0]["chunk_id"] == target["chunk_id"]
    assert hits[0]["score"] == pytest.approx(1.0, abs=1e-4)


def test_pipeline_fails_gracefully_on_empty_crawl(_tmp_data):
    result = process_pages("empty", SEED, _crawl_result([], failed=3), embed_fn=_embed)
    assert result.status == "failed"
    assert result.error
    assert not (_tmp_data / "empty").exists()

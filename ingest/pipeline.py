"""Ingestion orchestrator (Dev A — Milestone 4).

Wires the whole pipeline: validate -> crawl -> clean -> detect services ->
chunk -> embed -> build FAISS index -> write manifest. A session is only marked
`ready` when it has a non-empty index; otherwise it is `failed` (§28).

`process_pages` is network-free (accepts already-crawled pages and an injectable
embed function), so the assembly + reload can be tested without AWS/network.

The UI drives this in two user-visible phases rather than one opaque call:
  1. `save_sitemap` - crawl the site and persist the discovered/failed URLs (the
     "site map") plus staged raw HTML, WITHOUT embedding/indexing anything yet.
  2. `build_from_sitemap` - reload that staged crawl and run the rest of the
     pipeline (clean -> detect -> chunk -> embed -> index) to actually produce
     the queryable knowledge base.
`run_ingestion` still does both in one call, kept for scripts/tests that want a
single-shot pipeline.
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from app import config
from app.vectorstore import build_index
from ingest.chunker import chunk_documents
from ingest.crawler import CrawlFailure, CrawlResult, RetainedPage, crawl
from ingest.extractor import clean_page, persist_page
from ingest.service_detector import detect_services

EmbedFn = Callable[[list[str]], list[list[float]]]


@dataclass
class IngestionResult:
    session_id: str
    status: str  # "ready" | "failed"
    pages_discovered: int = 0
    pages_retained: int = 0
    pages_failed: int = 0
    chunks: int = 0
    services: list[str] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    error: str | None = None


def _default_embed(texts: list[str]) -> list[list[float]]:
    # Lazy import so offline runs with a custom embed_fn need no boto3/creds.
    from app import bedrock

    return bedrock.embed_texts(texts)


def process_pages(
    session_id: str,
    seed_url: str,
    crawl_result: CrawlResult,
    *,
    embed_fn: EmbedFn | None = None,
    embedding_model: str = config.BEDROCK_EMBED_MODEL_ID,
) -> IngestionResult:
    """Clean -> detect -> chunk -> embed -> build index for crawled pages.

    Network-free given a CrawlResult. Returns a failed result (no index written)
    when there is nothing usable to index (§28).
    """
    pages: list[RetainedPage] = crawl_result.pages
    result = IngestionResult(
        session_id=session_id,
        status="failed",
        pages_discovered=crawl_result.discovered,
        pages_retained=crawl_result.pages_retained,
        pages_failed=crawl_result.pages_failed,
    )

    if not pages:
        result.error = "No usable pages were retained."
        return result

    documents = []
    for page in pages:
        doc = clean_page(page, session_id)
        persist_page(session_id, doc, page.html)
        documents.append(doc)

    catalog = detect_services(documents)
    service_names = [s["name"] for s in catalog]

    chunks = chunk_documents(documents)
    if not chunks:
        result.error = "Pages retained but produced no chunks."
        return result

    embeddings = (embed_fn or _default_embed)([c["text"] for c in chunks])

    manifest = {
        "session_id": session_id,
        "base_url": seed_url,
        "normalized_host": urlsplit(seed_url).hostname or crawl_result.host,
        "pages_discovered": crawl_result.discovered,
        "pages_retained": crawl_result.pages_retained,
        "pages_failed": crawl_result.pages_failed,
        "chunks": len(chunks),
        "services": service_names,
        "service_catalog": catalog,
        "embedding_model": embedding_model,
        "embedding_dimensions": config.EMBED_DIMENSIONS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "ready",
    }

    build_index(session_id, chunks, embeddings, manifest)

    result.status = "ready"
    result.chunks = len(chunks)
    result.services = service_names
    result.documents = [
        {"document_id": d.document_id, "url": d.source_url, "title": d.title, "service": d.service}
        for d in documents
    ]
    return result


def run_ingestion(
    seed_url: str,
    *,
    session_id: str | None = None,
    embed_fn: EmbedFn | None = None,
    include_www: bool = True,
) -> IngestionResult:
    """Full pipeline entry point: crawl a seed URL and build its knowledge base."""
    session_id = session_id or uuid.uuid4().hex[:12]
    crawl_result = crawl(seed_url, include_www=include_www)
    return process_pages(session_id, seed_url, crawl_result, embed_fn=embed_fn)


# --- Two-phase split: site map first, knowledge base build second --------------

def _sitemap_path(session_id: str) -> Path:
    return config.session_dir(session_id) / "sitemap.json"


def _pending_raw_dir(session_id: str) -> Path:
    return config.session_dir(session_id) / "_pending_raw"


def save_sitemap(session_id: str, crawl_result: CrawlResult) -> dict:
    """Persist the discovered site map (retained + failed URLs) and stage raw HTML
    so `build_from_sitemap` can build the knowledge base later without re-crawling.
    """
    pending = _pending_raw_dir(session_id)
    pending.mkdir(parents=True, exist_ok=True)

    pages = []
    for i, page in enumerate(crawl_result.pages):
        raw_name = f"page-{i}.html"
        (pending / raw_name).write_text(page.html, encoding="utf-8")
        pages.append({
            "url": page.url,
            "canonical_url": page.canonical_url,
            "status_code": page.status_code,
            "content_type": page.content_type,
            "depth": page.depth,
            "raw_file": raw_name,
        })

    sitemap = {
        "seed_url": crawl_result.seed_url,
        "host": crawl_result.host,
        "discovered": crawl_result.discovered,
        "pages": pages,
        "failures": [{"url": f.url, "reason": f.reason} for f in crawl_result.failures],
    }
    _sitemap_path(session_id).write_text(json.dumps(sitemap, ensure_ascii=False, indent=2), encoding="utf-8")
    return sitemap


def load_sitemap(session_id: str) -> dict:
    """Return the site map saved by `save_sitemap`, or raise if none exists yet."""
    path = _sitemap_path(session_id)
    if not path.exists():
        raise FileNotFoundError(f"No site map found for session '{session_id}' (expected {path}).")
    return json.loads(path.read_text(encoding="utf-8"))


def _crawl_result_from_sitemap(session_id: str, sitemap: dict) -> CrawlResult:
    pending = _pending_raw_dir(session_id)
    pages = [
        RetainedPage(
            url=p["url"],
            canonical_url=p["canonical_url"],
            status_code=p["status_code"],
            content_type=p["content_type"],
            html=(pending / p["raw_file"]).read_text(encoding="utf-8"),
            depth=p["depth"],
        )
        for p in sitemap["pages"]
    ]
    failures = [CrawlFailure(f["url"], f["reason"]) for f in sitemap["failures"]]
    return CrawlResult(
        seed_url=sitemap["seed_url"], host=sitemap["host"],
        pages=pages, failures=failures, discovered=sitemap["discovered"],
    )


def build_from_sitemap(
    session_id: str,
    *,
    embed_fn: EmbedFn | None = None,
    embedding_model: str = config.BEDROCK_EMBED_MODEL_ID,
) -> IngestionResult:
    """Phase 2: turn a previously-saved site map into a queryable knowledge base."""
    sitemap = load_sitemap(session_id)
    crawl_result = _crawl_result_from_sitemap(session_id, sitemap)
    result = process_pages(
        session_id, sitemap["seed_url"], crawl_result, embed_fn=embed_fn, embedding_model=embedding_model,
    )
    shutil.rmtree(_pending_raw_dir(session_id), ignore_errors=True)
    return result

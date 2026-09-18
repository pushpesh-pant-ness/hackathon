"""Ingestion orchestrator (Dev A — Milestone 4).

Wires the whole pipeline: validate -> crawl -> clean -> detect services ->
chunk -> embed -> build FAISS index -> write manifest. A session is only marked
`ready` when it has a non-empty index; otherwise it is `failed` (§28).

`process_pages` is network-free (accepts already-crawled pages and an injectable
embed function), so the assembly + reload can be tested without AWS/network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.config import settings
from app.vectorstore import build_index
from ingest.chunker import chunk_documents
from ingest.crawler import CrawlResult, RetainedPage, crawl
from ingest.extractor import clean_page, persist_page
from ingest.service_detector import detect_services

EmbedFn = Callable[[list[str]], list[list[float]]]


class IngestionNotImplemented(NotImplementedError):
    """Retained so app/main.py's legacy fixture-fallback branch still resolves.

    The real pipeline is implemented, so this is no longer raised.
    """


class IngestionError(RuntimeError):
    """Raised when a crawl produces no usable knowledge base (§28)."""


@dataclass
class IngestionResult:
    session_id: str
    status: str  # "ready" | "failed"
    pages_discovered: int = 0
    pages_retained: int = 0
    pages_failed: int = 0
    chunks: int = 0
    services: list[str] = field(default_factory=list)
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
    embedding_model: str | None = None,
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
        "embedding_model": embedding_model or settings.titan_model_id,
        "embedding_dimensions": settings.embedding_dimensions,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "ready",
    }

    build_index(session_id, chunks, embeddings, manifest)

    result.status = "ready"
    result.chunks = len(chunks)
    result.services = service_names
    return result


def run_ingestion(
    session_id: str,
    url: str,
    session_dir=None,
    *,
    embed_fn: EmbedFn | None = None,
    include_www: bool = True,
) -> dict:
    """Crawl `url` and build its session-scoped knowledge base.

    Matches the contract app/main.py depends on: returns a dict of counts and
    raises on failure so the caller marks the session failed (§28). `session_dir`
    is accepted for signature compatibility; paths are derived from settings.
    """
    crawl_result = crawl(url, include_www=include_www)
    result = process_pages(session_id, url, crawl_result, embed_fn=embed_fn)
    if result.status != "ready":
        raise IngestionError(result.error or "Ingestion failed.")
    return {
        "pages_discovered": result.pages_discovered,
        "pages_retained": result.pages_retained,
        "pages_failed": result.pages_failed,
        "chunks": result.chunks,
        "services": result.services,
    }

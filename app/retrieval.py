"""Query embedding + session-scoped FAISS top-k search + evidence gate.

Reads the index.faiss/chunks.json files Dev A's pipeline (or the fixture builder)
writes under data/sessions/<id>/. Deliberately does NOT touch app/vectorstore.py
(Dev A's build-side module) - this is the read-only query path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import faiss
import numpy as np

from app.bedrock import embed_text
from app.config import settings

_INDEX_CACHE: dict[str, tuple[faiss.Index, list[dict]]] = {}


class SessionIndexNotFound(FileNotFoundError):
    pass


def invalidate_cache(session_id: str) -> None:
    _INDEX_CACHE.pop(session_id, None)


def _load_index(session_id: str) -> tuple[faiss.Index, list[dict]]:
    if session_id in _INDEX_CACHE:
        return _INDEX_CACHE[session_id]

    session_dir: Path = settings.session_dir(session_id)
    index_path = session_dir / "index.faiss"
    chunks_path = session_dir / "chunks.json"
    if not index_path.exists() or not chunks_path.exists():
        raise SessionIndexNotFound(
            f"No vector index for session '{session_id}' yet (expected {index_path})."
        )

    index = faiss.read_index(str(index_path))
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    _INDEX_CACHE[session_id] = (index, chunks)
    return index, chunks


def embed_query(text: str) -> list[float]:
    return embed_text(text)


def search(session_id: str, query: str, k: int | None = None, service: str | None = None) -> list[dict]:
    """Top-k chunk search, softly scoped by `service` when provided.

    Never returns empty just because the service filter is imperfect (FINAL_PLAN §19):
    falls back to the unfiltered ranking if nothing matches the requested service.
    """
    k = k or settings.retrieval_top_k
    index, chunks = _load_index(session_id)
    if index.ntotal == 0:
        return []

    query_vec = np.asarray([embed_query(query)], dtype=np.float32)
    fetch_k = min(index.ntotal, max(k * 4, 20))
    scores, positions = index.search(query_vec, fetch_k)

    candidates = [
        {**chunks[pos], "score": float(score)}
        for score, pos in zip(scores[0], positions[0])
        if pos != -1
    ]

    if service:
        scoped = [c for c in candidates if c.get("service") == service]
        if scoped:
            candidates = scoped

    return candidates[:k]


def _select_diverse(candidates: list[dict], k: int, max_per_source: int = 2) -> list[dict]:
    """Cap chunks-per-source so citations aren't all from a single page."""
    selected: list[dict] = []
    seen_chunk_ids: set[str] = set()
    per_source: dict[str, int] = {}
    for c in candidates:
        if c["chunk_id"] in seen_chunk_ids:
            continue
        src = c["source_url"]
        if per_source.get(src, 0) >= max_per_source:
            continue
        selected.append(c)
        seen_chunk_ids.add(c["chunk_id"])
        per_source[src] = per_source.get(src, 0) + 1
        if len(selected) >= k:
            break
    return selected


@dataclass
class RetrievalResult:
    query: str
    service: str | None
    candidates: list[dict] = field(default_factory=list)
    selected: list[dict] = field(default_factory=list)
    top_score: float = 0.0
    accepted: bool = False


def passes_evidence_gate(top_score: float, threshold: float | None = None) -> bool:
    """FINAL_PLAN_IMPROVED.md Section 20: never force an answer on weak evidence."""
    effective_threshold = settings.evidence_similarity_threshold if threshold is None else threshold
    return top_score >= effective_threshold


def retrieve(session_id: str, query: str, service: str | None = None) -> RetrievalResult:
    """Search then apply the evidence gate. The single entry point rag.py should call."""
    try:
        candidates = search(session_id, query, service=service)
    except SessionIndexNotFound:
        return RetrievalResult(query=query, service=service)

    top_score = candidates[0]["score"] if candidates else 0.0
    accepted = bool(candidates) and passes_evidence_gate(top_score)
    selected = _select_diverse(candidates, settings.retrieval_top_k_selected) if accepted else []

    return RetrievalResult(
        query=query,
        service=service,
        candidates=candidates,
        selected=selected,
        top_score=top_score,
        accepted=accepted,
    )

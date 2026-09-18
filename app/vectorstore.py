"""Session-scoped FAISS vector store (Dev A — Milestone 4).

Layout per session (see FINAL_PLAN_IMPROVED.md §15):
    data/sessions/<session_id>/
        index.faiss     FAISS IndexFlatIP over L2-normalized embeddings
        chunks.json     list[dict] chunk metadata, index-aligned with FAISS rows
        manifest.json   session summary (see §27)

Cosine similarity is achieved by L2-normalizing vectors and using inner product.
"""

from __future__ import annotations

import json
from pathlib import Path

import faiss
import numpy as np

from app import config


def _normalize(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype="float32")
    faiss.normalize_L2(vectors)
    return vectors


def build_index(
    session_id: str,
    chunks: list[dict],
    embeddings: list[list[float]],
    manifest: dict,
) -> Path:
    """Build and persist index.faiss, chunks.json and manifest.json for a session.

    chunks[i] must correspond to embeddings[i]. Raises if empty — a session must
    never be marked ready with an empty index (§28).
    """
    if not chunks or not embeddings:
        raise ValueError("Refusing to build an empty index (no chunks/embeddings).")
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings length mismatch.")

    matrix = _normalize(np.array(embeddings, dtype="float32"))
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)

    out = config.session_dir(session_id)
    out.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(out / "index.faiss"))
    (out / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


class SessionIndex:
    """Loaded, queryable knowledge base for one crawl session."""

    def __init__(self, session_id: str, index: faiss.Index, chunks: list[dict], manifest: dict):
        self.session_id = session_id
        self.index = index
        self.chunks = chunks
        self.manifest = manifest

    @classmethod
    def load(cls, session_id: str) -> "SessionIndex":
        d = config.session_dir(session_id)
        index = faiss.read_index(str(d / "index.faiss"))
        chunks = json.loads((d / "chunks.json").read_text(encoding="utf-8"))
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        return cls(session_id, index, chunks, manifest)

    def search(self, query_embedding: list[float], k: int = 8) -> list[dict]:
        """Return top-k chunk dicts, each with an added 'score' (cosine similarity)."""
        q = _normalize(np.array([query_embedding], dtype="float32"))
        scores, ids = self.index.search(q, min(k, len(self.chunks)))
        results = []
        for score, idx in zip(scores[0], ids[0]):
            if idx < 0:
                continue
            chunk = dict(self.chunks[idx])
            chunk["score"] = float(score)
            results.append(chunk)
        return results

"""Structure-aware chunking (Dev A — Milestone 3, §14).

Chunks each heading-delimited section separately so heading context is never
lost. A section that fits the budget becomes one chunk; larger sections are
split with token overlap. Every chunk carries full metadata (§13) and matches
the fixture chunk shape Dev B builds against.
"""

from __future__ import annotations

from app.config import settings
from ingest.extractor import CleanedDocument

# Rough words<->tokens conversion (~0.75 words per token for English prose).
_WORDS_PER_TOKEN = 0.75


def _split_words(text: str, target_tokens: int, overlap_tokens: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    budget = max(1, int(target_tokens * _WORDS_PER_TOKEN))
    if len(words) <= budget:
        return [" ".join(words)]

    overlap = min(int(overlap_tokens * _WORDS_PER_TOKEN), budget - 1)
    step = max(1, budget - overlap)
    pieces: list[str] = []
    i = 0
    while i < len(words):
        pieces.append(" ".join(words[i : i + budget]))
        if i + budget >= len(words):
            break
        i += step
    return pieces


def chunk_document(
    doc: CleanedDocument,
    target_tokens: int = None,
    overlap_tokens: int = None,
) -> list[dict]:
    """Return metadata-complete chunk dicts for one cleaned document (§13)."""
    target_tokens = target_tokens or settings.chunk_target_tokens
    overlap_tokens = overlap_tokens if overlap_tokens is not None else settings.chunk_overlap_tokens
    chunks: list[dict] = []
    idx = 0
    for section in doc.sections:
        heading = section["heading"]
        prefix = f"{doc.title} > {heading}" if heading else doc.title
        for piece in _split_words(section["text"], target_tokens, overlap_tokens):
            chunks.append({
                "chunk_id": f"{doc.document_id}-c{idx}",
                "document_id": doc.document_id,
                "chunk_index": idx,
                "text": f"{prefix}\n\n{piece}",
                "source_url": doc.source_url,
                "canonical_url": doc.canonical_url,
                "title": doc.title,
                "heading": heading,
                "service": doc.service,
                "crawl_session_id": doc.crawl_session_id,
            })
            idx += 1
    return chunks


def chunk_documents(
    documents: list[CleanedDocument],
    target_tokens: int = None,
    overlap_tokens: int = None,
) -> list[dict]:
    chunks: list[dict] = []
    for doc in documents:
        chunks.extend(chunk_document(doc, target_tokens, overlap_tokens))
    return chunks

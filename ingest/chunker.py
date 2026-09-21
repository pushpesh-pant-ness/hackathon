"""Structure-aware chunking (Dev A — Milestone 3, §14).

Chunks each heading-delimited section separately so heading context is never
lost. A section that fits the budget becomes one chunk; larger sections are
split with token overlap. Every chunk carries full metadata (§13) and matches
the fixture chunk shape Dev B builds against.
"""

from __future__ import annotations

from app import config
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


def _pack_paragraphs(paragraphs: list[str], target_tokens: int, overlap_tokens: int) -> list[str]:
    """Group whole paragraphs up to the token budget (§14: "paragraph groups"
    before "target chunk size"), instead of slicing raw word counts, so a chunk
    only ever splits mid-paragraph when a single paragraph alone exceeds the
    budget. The last paragraph of a chunk is carried into the next one for
    continuity, mirroring `_split_words`'s word-level overlap.
    """
    budget = max(1, int(target_tokens * _WORDS_PER_TOKEN))
    pieces: list[str] = []
    group: list[str] = []
    group_words = 0

    def flush() -> None:
        if group:
            pieces.append("\n\n".join(group))

    for para in paragraphs:
        words = para.split()
        if not words:
            continue
        if len(words) > budget:
            flush()
            group, group_words = [], 0
            pieces.extend(_split_words(para, target_tokens, overlap_tokens))
            continue
        if group and group_words + len(words) > budget:
            flush()
            carry = group[-1] if overlap_tokens > 0 else None
            group = [carry] if carry else []
            group_words = len(carry.split()) if carry else 0
        group.append(para)
        group_words += len(words)
    flush()
    return pieces


def chunk_document(
    doc: CleanedDocument,
    target_tokens: int = config.CHUNK_TARGET_TOKENS,
    overlap_tokens: int = config.CHUNK_OVERLAP_TOKENS,
) -> list[dict]:
    """Return metadata-complete chunk dicts for one cleaned document (§13)."""
    chunks: list[dict] = []
    idx = 0
    for section in doc.sections:
        heading = section["heading"]
        prefix = f"{doc.title} > {heading}" if heading else doc.title
        paragraphs = section["text"].split("\n\n")
        for piece in _pack_paragraphs(paragraphs, target_tokens, overlap_tokens):
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
    target_tokens: int = config.CHUNK_TARGET_TOKENS,
    overlap_tokens: int = config.CHUNK_OVERLAP_TOKENS,
) -> list[dict]:
    chunks: list[dict] = []
    for doc in documents:
        chunks.extend(chunk_document(doc, target_tokens, overlap_tokens))
    return chunks

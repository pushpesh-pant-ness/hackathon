"""HTML cleaning + title/heading/content extraction (Dev A — Milestone 3, §11).

Turns a retained raw page into a `CleanedDocument`: title, heading-delimited
sections, and normalized full text. Sections preserve document structure so the
chunker can keep heading context (§14). Also persists raw + cleaned copies.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from app import config
from ingest.crawler import RetainedPage

# Structural/boilerplate elements dropped before extraction.
_STRIP_TAGS = ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "template"]
_BLOCK_TAGS = ["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote", "dd", "dt"]
_HEADING_TAGS = {"h1", "h2", "h3", "h4"}
_WS = re.compile(r"\s+")


@dataclass
class CleanedDocument:
    document_id: str
    source_url: str
    canonical_url: str
    title: str
    headings: list[str]
    sections: list[dict]  # [{"heading": str | None, "text": str}]
    text: str
    crawl_session_id: str
    scraped_at: str
    service: str | None = None


def _document_id(canonical_url: str) -> str:
    digest = hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()[:12]
    return f"doc-{digest}"


def _normalize(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _extract_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        title = _normalize(soup.title.string)
        if title:
            return title
    h1 = soup.find("h1")
    if h1:
        return _normalize(h1.get_text(" ", strip=True))
    return "Untitled"


def _extract_sections(soup: BeautifulSoup) -> list[dict]:
    """Group block text under the most recent heading, in document order."""
    container = soup.find("main") or soup.find("article") or soup.body or soup
    sections: list[dict] = []
    current: dict = {"heading": None, "parts": []}

    for el in container.find_all(_BLOCK_TAGS):
        text = _normalize(el.get_text(" ", strip=True))
        if not text:
            continue
        if el.name in _HEADING_TAGS:
            if current["parts"]:
                sections.append({"heading": current["heading"], "text": "\n\n".join(current["parts"])})
            current = {"heading": text, "parts": []}
        else:
            current["parts"].append(text)

    if current["parts"]:
        sections.append({"heading": current["heading"], "text": "\n\n".join(current["parts"])})
    return sections


def clean_page(page: RetainedPage, session_id: str) -> CleanedDocument:
    soup = BeautifulSoup(page.html, "lxml")
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()

    title = _extract_title(soup)
    sections = _extract_sections(soup)
    headings = [s["heading"] for s in sections if s["heading"]]
    full_text = "\n\n".join(
        (f"{s['heading']}\n{s['text']}" if s["heading"] else s["text"]) for s in sections
    )

    return CleanedDocument(
        document_id=_document_id(page.canonical_url),
        source_url=page.url,
        canonical_url=page.canonical_url,
        title=title,
        headings=headings,
        sections=sections,
        text=full_text,
        crawl_session_id=session_id,
        scraped_at=datetime.now(timezone.utc).isoformat(),
    )


def persist_page(session_id: str, doc: CleanedDocument, raw_html: str) -> None:
    """Write raw HTML and cleaned text under the session directory (§11, §15)."""
    d = config.session_dir(session_id)
    (d / "raw").mkdir(parents=True, exist_ok=True)
    (d / "cleaned").mkdir(parents=True, exist_ok=True)
    (d / "raw" / f"{doc.document_id}.html").write_text(raw_html, encoding="utf-8")
    (d / "cleaned" / f"{doc.document_id}.txt").write_text(doc.text, encoding="utf-8")

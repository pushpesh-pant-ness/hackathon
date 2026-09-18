"""Deterministic service discovery (Dev A — Milestone 3, §12).

Identifies service pages from URL structure and labels their documents, then
builds a service catalog. No per-page LLM call — deterministic and debuggable.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ingest.extractor import CleanedDocument

# Path segments that mark a service/solution area of a site.
_SERVICE_MARKERS = {
    "services", "service", "solutions", "solution", "what-we-do",
    "products", "product", "capabilities", "offerings", "expertise",
}


def _humanize(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title()


def _service_from_path(path: str) -> str | None:
    """Return a service name if the path is a specific service page (not the index)."""
    segments = [s for s in path.split("/") if s]
    for i, seg in enumerate(segments):
        if seg.lower() in _SERVICE_MARKERS and i + 1 < len(segments):
            return _humanize(segments[i + 1])
    return None


def detect_services(documents: list[CleanedDocument]) -> list[dict]:
    """Label each document's `service` in place and return the catalog (§12).

    Returns [{"name": str, "urls": [str, ...]}, ...] sorted by name.
    """
    catalog: dict[str, list[str]] = {}
    for doc in documents:
        name = _service_from_path(urlsplit(doc.canonical_url).path)
        if not name:
            continue
        doc.service = name
        urls = catalog.setdefault(name, [])
        if doc.canonical_url not in urls:
            urls.append(doc.canonical_url)
    return [{"name": name, "urls": urls} for name, urls in sorted(catalog.items())]

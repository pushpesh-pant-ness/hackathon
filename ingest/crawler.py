"""Bounded same-domain BFS crawler (Dev A — Milestone 2, §8).

Guarantees:
  - hard cap of MAX_PAGES retained pages and MAX_DEPTH levels,
  - request timeout, response-size cap, polite crawl delay, real User-Agent,
  - manual redirect following that revalidates every hop's host (SSRF, §30),
  - retains only HTML pages with meaningful text (§9); records failures (§28).

Output feeds Milestone 3 (cleaning/chunking): each retained page carries its
final canonical URL, status, content-type and raw HTML.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from app import config
from ingest.canonicalizer import canonicalize_url
from ingest.robots import RobotsChecker
from ingest.url_validator import (
    UrlValidationError,
    safe_absolute_url,
    validate_seed_url,
)

# Asset/document extensions and non-http schemes we never enqueue (§9).
_SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff",
    ".mp4", ".webm", ".mov", ".avi", ".mp3", ".wav", ".ogg",
    ".css", ".js", ".json", ".xml", ".rss",
    ".pdf", ".zip", ".gz", ".tar", ".rar", ".7z", ".exe", ".dmg", ".pkg",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
}
_MAX_REDIRECTS = 5
_MIN_TEXT_CHARS = 200


@dataclass
class RetainedPage:
    url: str
    canonical_url: str
    status_code: int
    content_type: str
    html: str
    depth: int


@dataclass
class CrawlFailure:
    url: str
    reason: str


@dataclass
class CrawlResult:
    seed_url: str
    host: str
    pages: list[RetainedPage] = field(default_factory=list)
    failures: list[CrawlFailure] = field(default_factory=list)
    discovered: int = 0

    @property
    def pages_retained(self) -> int:
        return len(self.pages)

    @property
    def pages_failed(self) -> int:
        return len(self.failures)


def is_crawlable_link(url: str) -> bool:
    """Reject non-http(s) schemes and obvious asset/document URLs."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False
    path = parts.path.lower()
    dot = path.rfind(".")
    if dot != -1:
        ext = path[dot:]
        if ext in _SKIP_EXTENSIONS:
            return False
    return True


def extract_links(html: str, base_url: str, allowed_hosts: set[str]) -> list[str]:
    """Return canonical, same-host, crawlable links found in `html`."""
    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        canonical = canonicalize_url(href, base=base_url)
        if canonical is None or not is_crawlable_link(canonical):
            continue
        host = urlsplit(canonical).hostname
        if not host or host.lower() not in allowed_hosts:
            continue
        if canonical not in seen:
            seen.add(canonical)
            found.append(canonical)
    return found


def _has_meaningful_text(html: str) -> bool:
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    return len(text) >= _MIN_TEXT_CHARS


def _read_capped(resp: requests.Response, cap: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=8192):
        chunks.append(chunk)
        total += len(chunk)
        if total > cap:
            raise ValueError(f"response exceeds {cap} bytes")
    return b"".join(chunks)


def _fetch(session: requests.Session, url: str, allowed_hosts: set[str]) -> RetainedPage | None:
    """Fetch a URL, following redirects manually and revalidating each hop (§30).

    Returns a RetainedPage (without depth set) or None if the page is not
    retainable. Raises on validation/transport errors so the caller can record
    the failure.
    """
    current = safe_absolute_url(url, allowed_hosts)
    for _ in range(_MAX_REDIRECTS + 1):
        resp = session.get(
            current,
            timeout=config.REQUEST_TIMEOUT,
            allow_redirects=False,
            stream=True,
            headers={"User-Agent": config.USER_AGENT},
        )
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                raise ValueError("redirect without Location header")
            # Revalidate the redirect target (preserving its path) before following.
            current = safe_absolute_url(location, allowed_hosts, base=current)
            continue

        content_type = resp.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if resp.status_code >= 400:
            resp.close()
            raise ValueError(f"HTTP {resp.status_code}")
        if content_type and content_type != "text/html":
            resp.close()
            return None

        body = _read_capped(resp, config.MAX_RESPONSE_BYTES)
        resp.close()
        html = body.decode(resp.encoding or "utf-8", errors="replace")
        if not _has_meaningful_text(html):
            return None
        return RetainedPage(
            url=url,
            canonical_url=canonicalize_url(current) or current,
            status_code=resp.status_code,
            content_type=content_type or "text/html",
            html=html,
            depth=0,
        )

    raise ValueError("too many redirects")


def crawl(seed_url: str, *, include_www: bool = True) -> CrawlResult:
    """Run a bounded same-domain BFS starting at `seed_url`."""
    seed = validate_seed_url(seed_url, include_www=include_www)
    result = CrawlResult(seed_url=seed.canonical_url, host=seed.host)

    robots = RobotsChecker()
    session = requests.Session()

    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(seed.canonical_url, 0)])

    while queue and result.pages_retained < config.MAX_PAGES:
        url, depth = queue.popleft()
        if url in visited or depth > config.MAX_DEPTH:
            continue
        visited.add(url)
        result.discovered += 1

        if not robots.can_fetch(url):
            result.failures.append(CrawlFailure(url, "blocked by robots.txt"))
            continue

        try:
            page = _fetch(session, url, seed.allowed_hosts)
        except (UrlValidationError, ValueError, requests.RequestException) as exc:
            result.failures.append(CrawlFailure(url, str(exc)))
            continue

        if page is None:
            continue

        page.depth = depth
        result.pages.append(page)

        if depth < config.MAX_DEPTH and result.pages_retained < config.MAX_PAGES:
            for link in extract_links(page.html, page.canonical_url, seed.allowed_hosts):
                if link not in visited:
                    queue.append((link, depth + 1))

        if config.CRAWL_DELAY_SECONDS:
            time.sleep(config.CRAWL_DELAY_SECONDS)

    return result

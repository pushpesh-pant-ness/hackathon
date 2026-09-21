"""Optional headless-browser rendering for JavaScript-heavy pages (stretch goal, §36.2).

Static `requests` fetches (ingest/crawler.py) never execute JavaScript, so pages
that render their content client-side (SPA shells, lazy-loaded sections, JS-only
navigation) look empty or near-empty to the crawler. This module renders a URL
with a headless Chromium browser and returns the post-JS-execution HTML, so the
same extraction/chunking pipeline can pick up whatever the static fetch missed.

Playwright is an optional dependency (`pip install playwright` then
`playwright install chromium`): if the package or its browser binaries aren't
installed, every function here returns None/no-ops instead of raising, so the
crawler just keeps the static HTML it already had. This feature only ever adds
information - it can never make a crawl worse or block it.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app import config
from ingest.url_validator import is_host_allowed

_browser = None
_playwright_ctx = None
_unavailable = False  # sticky after the first failure so we stop retrying mid-crawl

# Resource types that never affect extracted text - blocking them speeds up rendering.
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


def _get_browser():
    """Lazily launch one headless Chromium instance, shared across a whole crawl."""
    global _browser, _playwright_ctx, _unavailable
    if _unavailable:
        return None
    if _browser is not None:
        return _browser
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _unavailable = True
        return None
    try:
        _playwright_ctx = sync_playwright().start()
        _browser = _playwright_ctx.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
    except Exception:
        # Browser binaries missing (needs `playwright install chromium`) or launch failed
        # for any other reason (e.g. no display deps in a locked-down container).
        if _playwright_ctx is not None:
            _playwright_ctx.stop()
        _playwright_ctx = None
        _browser = None
        _unavailable = True
    return _browser


def shutdown() -> None:
    """Release the shared browser/Playwright process. Call once a crawl finishes."""
    global _browser, _playwright_ctx, _unavailable
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
    if _playwright_ctx is not None:
        try:
            _playwright_ctx.stop()
        except Exception:
            pass
    _browser = None
    _playwright_ctx = None
    _unavailable = False


def _route_handler(allowed_hosts: set[str]):
    """Block heavy assets (perf) and cross-site navigation (§30 SSRF: a page's own
    JS must not be able to redirect the browser to a disallowed host). Subresource
    fetches (xhr/fetch/script) to other public hosts, e.g. CDNs/APIs, are left
    alone - see README "Known limitations" for the accepted scope of this check.
    """

    def _handle(route) -> None:
        request = route.request
        if request.resource_type in _BLOCKED_RESOURCE_TYPES:
            route.abort()
            return
        if request.resource_type == "document":
            host = urlsplit(request.url).hostname
            if not is_host_allowed(host, allowed_hosts):
                route.abort()
                return
        route.continue_()

    return _handle


def render_page(url: str, allowed_hosts: set[str]) -> str | None:
    """Return post-JS HTML for `url`, or None if rendering is unavailable/fails.

    Re-validates the URL the browser actually lands on (after any client-side
    navigation) against `allowed_hosts` before returning content, mirroring the
    redirect-revalidation the static fetch path already does.
    """
    browser = _get_browser()
    if browser is None:
        return None
    try:
        page = browser.new_page(user_agent=config.USER_AGENT)
    except Exception:
        return None
    try:
        page.set_default_timeout(config.JS_RENDER_TIMEOUT_MS)
        page.route("**/*", _route_handler(allowed_hosts))
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception:
            return None
        try:
            # Best-effort: give in-flight JS a chance to finish rendering, but use
            # whatever DOM exists so far if the page never truly goes idle.
            page.wait_for_load_state("networkidle", timeout=config.JS_RENDER_TIMEOUT_MS)
        except Exception:
            pass

        final_host = urlsplit(page.url).hostname
        if not is_host_allowed(final_host, allowed_hosts):
            return None
        return page.content()
    except Exception:
        return None
    finally:
        try:
            page.close()
        except Exception:
            pass

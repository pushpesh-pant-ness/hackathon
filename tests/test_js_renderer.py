"""Tests for the optional headless-JS-rendering fallback (ingest/js_renderer.py).

Fully offline: they force the "Playwright not importable" path via sys.modules so
no real browser or network is ever touched, and assert the module degrades to
returning None (the crawler just keeps its static HTML) instead of raising.
"""

from __future__ import annotations

import sys

import pytest

from ingest import js_renderer


@pytest.fixture(autouse=True)
def _reset_module_state():
    js_renderer.shutdown()
    yield
    js_renderer.shutdown()


def test_get_browser_returns_none_when_playwright_not_importable(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    assert js_renderer._get_browser() is None
    assert js_renderer._unavailable is True


def test_get_browser_does_not_retry_after_marked_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    assert js_renderer._get_browser() is None
    # Remove the sentinel; a real (non-degraded) import would now succeed if attempted,
    # so getting None again proves the "sticky" unavailable flag skipped re-importing.
    monkeypatch.delitem(sys.modules, "playwright.sync_api", raising=False)
    assert js_renderer._get_browser() is None


def test_render_page_returns_none_when_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    assert js_renderer.render_page("https://example.com", {"example.com"}) is None


def test_shutdown_is_safe_when_nothing_was_launched():
    js_renderer.shutdown()  # must not raise
    assert js_renderer._browser is None
    assert js_renderer._unavailable is False

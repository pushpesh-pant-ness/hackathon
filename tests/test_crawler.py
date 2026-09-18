"""Tests for the crawler helpers and SSRF validation (Dev A — Milestone 2).

These are offline: they exercise pure helpers and IP-literal / loopback checks
that resolve without network access. Live crawling is validated manually.
"""

from __future__ import annotations

import pytest

from ingest.crawler import extract_links, is_crawlable_link
from ingest.url_validator import (
    UrlValidationError,
    assert_fetchable,
    build_allowed_hosts,
    is_host_allowed,
    is_safe_public_host,
)


# --- Host allow-listing --------------------------------------------------
def test_build_allowed_hosts_adds_www_variant():
    assert build_allowed_hosts("example.com") == {"example.com", "www.example.com"}
    assert build_allowed_hosts("www.example.com") == {"example.com", "www.example.com"}


def test_is_host_allowed_is_exact_not_suffix():
    allowed = {"example.com", "www.example.com"}
    assert is_host_allowed("example.com", allowed)
    # endswith-style bypass must be rejected.
    assert not is_host_allowed("evil-example.com", allowed)
    assert not is_host_allowed("example.com.attacker.com", allowed)
    assert not is_host_allowed("google.com", allowed)


# --- SSRF: private / loopback / link-local must be blocked ----------------
@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "localhost", "10.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "0.0.0.0"],
)
def test_is_safe_public_host_blocks_internal(host):
    ok, _reason = is_safe_public_host(host)
    assert ok is False


def test_assert_fetchable_rejects_disallowed_host():
    with pytest.raises(UrlValidationError):
        assert_fetchable("https://google.com/x", {"example.com"})


def test_assert_fetchable_rejects_non_http():
    with pytest.raises(UrlValidationError):
        assert_fetchable("mailto:hi@example.com", {"example.com"})


def test_assert_fetchable_blocks_internal_redirect_target():
    # Simulates revalidating a redirect Location pointing at metadata service.
    with pytest.raises(UrlValidationError):
        assert_fetchable("http://169.254.169.254/latest/meta-data", {"169.254.169.254"})


# --- Link filtering -------------------------------------------------------
def test_is_crawlable_link_skips_assets():
    assert is_crawlable_link("https://example.com/page")
    assert not is_crawlable_link("https://example.com/logo.png")
    assert not is_crawlable_link("https://example.com/app.js")
    assert not is_crawlable_link("https://example.com/report.pdf")
    assert not is_crawlable_link("ftp://example.com/x")


def test_extract_links_same_host_only_and_canonicalized():
    html = """
        <a href="/about/">About</a>
        <a href="/about#team">About dup</a>
        <a href="https://example.com/services?utm_source=x">Services</a>
        <a href="https://other.com/x">External</a>
        <a href="mailto:hi@example.com">Mail</a>
        <a href="/logo.png">Logo</a>
    """
    links = extract_links(html, "https://example.com/", {"example.com"})
    assert links == ["https://example.com/about", "https://example.com/services"]

"""Tests for URL canonicalization (Dev A — Milestone 2, §7)."""

from __future__ import annotations

from ingest.canonicalizer import canonicalize_url, registrable_host


def test_removes_fragment():
    assert canonicalize_url("https://example.com/about#team") == "https://example.com/about"


def test_normalizes_trailing_slash_but_keeps_root():
    assert canonicalize_url("https://example.com/about/") == "https://example.com/about"
    assert canonicalize_url("https://example.com/") == "https://example.com/"
    assert canonicalize_url("https://example.com") == "https://example.com/"


def test_lowercases_scheme_and_host():
    assert canonicalize_url("HTTPS://Example.COM/About") == "https://example.com/About"


def test_strips_tracking_params():
    assert (
        canonicalize_url("https://example.com/p?utm_source=x&id=5&gclid=abc")
        == "https://example.com/p?id=5"
    )


def test_drops_default_ports_keeps_custom():
    assert canonicalize_url("https://example.com:443/x") == "https://example.com/x"
    assert canonicalize_url("http://example.com:8080/x") == "http://example.com:8080/x"


def test_resolves_relative_against_base():
    assert (
        canonicalize_url("/services", base="https://example.com/about")
        == "https://example.com/services"
    )


def test_rejects_non_http_schemes():
    assert canonicalize_url("mailto:hi@example.com") is None
    assert canonicalize_url("javascript:alert(1)") is None
    assert canonicalize_url("tel:+15550100") is None
    assert canonicalize_url("ftp://example.com/file") is None


def test_dedup_equivalent_urls_collapse():
    a = canonicalize_url("https://example.com/about/")
    b = canonicalize_url("https://example.com/about#team")
    c = canonicalize_url("https://example.com/about?utm_source=x")
    assert a == b == c


def test_registrable_host():
    assert registrable_host("https://WWW.Example.com/x") == "www.example.com"
    assert registrable_host("notaurl") is None

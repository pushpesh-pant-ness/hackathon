"""URL canonicalization (Dev A — Milestone 2, §7).

Produces a single canonical representation for a URL so that BFS deduplication
against `visited_urls` is reliable. Host allow-listing and SSRF checks live in
`url_validator`; this module only normalizes.

Canonicalization steps (§7):
  1. Resolve relative URLs against a base.
  2. Lowercase scheme and hostname.
  3. Reject non-http(s) URLs.
  4. Remove the fragment.
  5. Drop default ports (80/443).
  6. Strip obvious tracking params (utm_*, gclid, fbclid, ...).
  7. Normalize the trailing slash (kept only for the root path).
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urldefrag, urljoin, urlencode, urlsplit, urlunsplit

_ALLOWED_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": "80", "https": "443"}

# Exact tracking keys and prefixes to drop from the query string.
_TRACKING_KEYS = {"gclid", "fbclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src"}
_TRACKING_PREFIXES = ("utm_",)


def _strip_tracking(query: str) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    kept = [
        (k, v)
        for k, v in pairs
        if k.lower() not in _TRACKING_KEYS
        and not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)
    ]
    return urlencode(kept)


def canonicalize_url(url: str, base: str | None = None) -> str | None:
    """Return the canonical form of `url`, or None if it is not a usable http(s) URL.

    If `base` is provided, relative URLs are resolved against it first.
    """
    if not url or not url.strip():
        return None

    url = url.strip()
    if base:
        url = urljoin(base, url)

    url, _frag = urldefrag(url)
    parts = urlsplit(url)

    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        return None

    host = parts.hostname.lower() if parts.hostname else ""
    if not host:
        return None

    # Preserve non-default ports only.
    port = parts.port
    netloc = host
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"

    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    query = _strip_tracking(parts.query)

    return urlunsplit((scheme, netloc, path, query, ""))


def registrable_host(url: str) -> str | None:
    """Return the lowercased hostname of a URL, or None if absent."""
    host = urlsplit(url).hostname
    return host.lower() if host else None

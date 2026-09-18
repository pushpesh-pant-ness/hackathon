"""URL validation + SSRF safety (Dev A — Milestone 2, §29 / §30).

Because the crawl seed comes from the user, every URL we might fetch — the seed
AND every redirect hop — must be revalidated:

  - scheme is http/https,
  - hostname is in an explicit allowed-host set (never `endswith`),
  - all resolved IPs are public (reject loopback/private/link-local/reserved).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from ingest.canonicalizer import canonicalize_url, registrable_host


class UrlValidationError(ValueError):
    """Raised when a seed URL cannot be accepted for crawling."""


@dataclass
class SeedValidation:
    canonical_url: str
    host: str
    allowed_hosts: set[str] = field(default_factory=set)


def build_allowed_hosts(host: str, *, include_www: bool = True) -> set[str]:
    """Explicit allow-set for a seed host, optionally including its www. variant."""
    host = host.lower()
    allowed = {host}
    if include_www:
        if host.startswith("www."):
            allowed.add(host[4:])
        else:
            allowed.add(f"www.{host}")
    return allowed


def is_host_allowed(host: str | None, allowed_hosts: set[str]) -> bool:
    """Exact membership check against the allow-set — no suffix matching."""
    return bool(host) and host.lower() in allowed_hosts


def _resolved_ips(host: str) -> list[ipaddress._BaseAddress]:
    infos = socket.getaddrinfo(host, None)
    ips: list[ipaddress._BaseAddress] = []
    for info in infos:
        addr = info[4][0]
        # Strip IPv6 zone id if present (e.g. fe80::1%eth0).
        addr = addr.split("%", 1)[0]
        ips.append(ipaddress.ip_address(addr))
    return ips


def _is_public_ip(ip: ipaddress._BaseAddress) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def is_safe_public_host(host: str) -> tuple[bool, str]:
    """Resolve `host` and confirm every resolved IP is public.

    Returns (ok, reason). Fails closed on DNS errors and on any non-public IP.
    """
    if not host:
        return False, "missing host"
    try:
        ips = _resolved_ips(host)
    except socket.gaierror as exc:
        return False, f"DNS resolution failed: {exc}"
    if not ips:
        return False, "no DNS records"
    for ip in ips:
        if not _is_public_ip(ip):
            return False, f"non-public address {ip}"
    return True, "ok"


def assert_fetchable(url: str, allowed_hosts: set[str]) -> str:
    """Validate a URL about to be fetched (seed or redirect target).

    Returns the canonical URL, or raises UrlValidationError. Apply this to every
    redirect hop, not just the original URL (§30).
    """
    canonical = canonicalize_url(url)
    if canonical is None:
        raise UrlValidationError(f"Not an http(s) URL: {url!r}")

    host = registrable_host(canonical)
    if not is_host_allowed(host, allowed_hosts):
        raise UrlValidationError(f"Host {host!r} not in allowed set {sorted(allowed_hosts)}")

    ok, reason = is_safe_public_host(host)
    if not ok:
        raise UrlValidationError(f"Blocked host {host!r}: {reason}")

    return canonical


def safe_absolute_url(url: str, allowed_hosts: set[str], base: str | None = None) -> str:
    """Resolve + host/SSRF-validate a URL for fetching, preserving its exact path.

    Unlike `assert_fetchable`, this does NOT canonicalize (no trailing-slash or
    tracking-param normalization), so following a server's redirect Location
    cannot fight the canonicalizer and loop. Use for every redirect hop (§30).
    """
    absolute = urljoin(base, url) if base else url
    parts = urlsplit(absolute)
    if parts.scheme.lower() not in ("http", "https"):
        raise UrlValidationError(f"Not an http(s) URL: {absolute!r}")

    host = parts.hostname.lower() if parts.hostname else None
    if not is_host_allowed(host, allowed_hosts):
        raise UrlValidationError(f"Host {host!r} not in allowed set {sorted(allowed_hosts)}")

    ok, reason = is_safe_public_host(host)
    if not ok:
        raise UrlValidationError(f"Blocked host {host!r}: {reason}")

    return absolute


def validate_seed_url(url: str, *, include_www: bool = True) -> SeedValidation:
    """Validate the user-supplied seed URL and derive its allowed-host set."""
    canonical = canonicalize_url(url)
    if canonical is None:
        raise UrlValidationError(f"Enter a valid http(s) URL (got {url!r}).")

    host = registrable_host(canonical)
    if not host:
        raise UrlValidationError("URL is missing a hostname.")

    allowed = build_allowed_hosts(host, include_www=include_www)

    ok, reason = is_safe_public_host(host)
    if not ok:
        raise UrlValidationError(f"Refusing to crawl {host!r}: {reason}")

    return SeedValidation(canonical_url=canonical, host=host, allowed_hosts=allowed)

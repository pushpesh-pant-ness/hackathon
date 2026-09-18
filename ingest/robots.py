"""robots.txt fetch + allow/deny check (Dev A — Milestone 2, §10).

Fetches and caches robots.txt per host and answers can_fetch(url). Fails open
(allow) when robots.txt is missing or unreachable, which is standard crawler
courtesy behavior for the hackathon.
"""

from __future__ import annotations

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

from app.config import settings


class RobotsChecker:
    """Caches one parsed robots.txt per (scheme, host)."""

    def __init__(self, user_agent: str = None, timeout: int = None):
        self.user_agent = user_agent or settings.user_agent
        self.timeout = timeout or settings.request_timeout
        self._cache: dict[str, RobotFileParser | None] = {}

    def _parser_for(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        if key in self._cache:
            return self._cache[key]

        robots_url = f"{key}/robots.txt"
        parser: RobotFileParser | None = RobotFileParser()
        try:
            resp = requests.get(
                robots_url,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            )
            if resp.status_code >= 400:
                # No usable robots.txt -> allow everything.
                parser = None
            else:
                parser.parse(resp.text.splitlines())
        except requests.RequestException:
            parser = None

        self._cache[key] = parser
        return parser

    def can_fetch(self, url: str) -> bool:
        parser = self._parser_for(url)
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        parser = self._parser_for(url)
        if parser is None:
            return None
        delay = parser.crawl_delay(self.user_agent)
        return float(delay) if delay is not None else None

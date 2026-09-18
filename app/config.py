"""Central configuration for the backend. Loads .env once and exposes typed settings.

Both Dev A (ingest/) and Dev B (app/, ui/) import from here so paths, model IDs, and
tuning knobs stay in one place instead of being hardcoded per module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    # AWS / Bedrock
    aws_access_key_id: str = field(default_factory=lambda: _env_str("ACCESS_KEY_ID", ""))
    aws_secret_access_key: str = field(default_factory=lambda: _env_str("SECRET_ACCESS_KEY", ""))
    aws_region: str = field(default_factory=lambda: _env_str("AWS_REGION", "us-east-1"))
    nova_model_id: str = field(default_factory=lambda: _env_str("NOVA_MODEL_ID", "amazon.nova-pro-v1:0"))
    titan_model_id: str = field(default_factory=lambda: _env_str("TITAN_MODEL_ID", "amazon.titan-embed-text-v2:0"))
    embedding_dimensions: int = field(default_factory=lambda: _env_int("EMBEDDING_DIMENSIONS", 1024))
    # "bedrock" (default, real Titan calls) or "local" (deterministic offline hash-embedding,
    # used by tests / fixture-building when there's no live AWS access).
    embedding_backend: str = field(default_factory=lambda: _env_str("EMBEDDING_BACKEND", "bedrock"))

    # Retrieval tuning
    retrieval_top_k: int = field(default_factory=lambda: _env_int("RETRIEVAL_TOP_K", 10))
    retrieval_top_k_selected: int = field(default_factory=lambda: _env_int("RETRIEVAL_TOP_K_SELECTED", 5))
    evidence_similarity_threshold: float = field(
        default_factory=lambda: _env_float("EVIDENCE_SIMILARITY_THRESHOLD", 0.35)
    )

    # Crawl limits (Dev A owns the enforcement, Dev B reads them for status/analytics display)
    max_pages: int = field(default_factory=lambda: _env_int("MAX_PAGES", 20))
    max_crawl_depth: int = field(default_factory=lambda: _env_int("MAX_CRAWL_DEPTH", 3))
    request_timeout: int = field(default_factory=lambda: _env_int("REQUEST_TIMEOUT", 10))
    max_response_bytes: int = field(default_factory=lambda: _env_int("MAX_RESPONSE_BYTES", 2_000_000))
    crawl_delay_seconds: float = field(default_factory=lambda: _env_float("CRAWL_DELAY_SECONDS", 0.5))
    user_agent: str = field(default_factory=lambda: _env_str("USER_AGENT", "HackathonRAGBot/0.1"))
    chunk_target_tokens: int = field(default_factory=lambda: _env_int("CHUNK_TARGET_TOKENS", 800))
    chunk_overlap_tokens: int = field(default_factory=lambda: _env_int("CHUNK_OVERLAP_TOKENS", 120))

    # Storage paths (resolved absolute, relative to repo root)
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / _env_str("DATA_DIR", "data"))
    sessions_dir: Path = field(default_factory=lambda: REPO_ROOT / _env_str("SESSIONS_DIR", "data/sessions"))
    sqlite_path: Path = field(default_factory=lambda: REPO_ROOT / _env_str("SQLITE_PATH", "data/app.db"))

    # UI wiring
    backend_url: str = field(default_factory=lambda: _env_str("BACKEND_URL", "http://localhost:8000"))

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()

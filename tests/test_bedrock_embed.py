"""Smoke test for the Titan Embed Text v2 wrapper (Dev A — Milestone 1).

Skips automatically when AWS credentials are not configured, so it is safe to
run in CI/offline. Run explicitly with real creds to verify Bedrock access:

    pytest tests/test_bedrock_embed.py -s
"""

from __future__ import annotations

import pytest

from app import bedrock
from app.config import settings

# Real Titan is only exercised when creds exist and the backend isn't the local stub.
_HAS_AWS = bool(settings.aws_access_key_id and settings.aws_secret_access_key)
_REAL_BACKEND = settings.embedding_backend != "local"
_RUN_LIVE = _HAS_AWS and _REAL_BACKEND


@pytest.mark.skipif(not _RUN_LIVE, reason="AWS credentials / bedrock backend not configured")
def test_embed_text_returns_expected_dimensions():
    vector = bedrock.embed_text("Hello from the hackathon RAG bot.")
    assert isinstance(vector, list)
    assert len(vector) == settings.embedding_dimensions
    assert all(isinstance(x, float) for x in vector)


@pytest.mark.skipif(not _RUN_LIVE, reason="AWS credentials / bedrock backend not configured")
def test_embed_texts_batches():
    vectors = bedrock.embed_texts(["alpha", "beta", "gamma"])
    assert len(vectors) == 3
    assert all(len(v) == settings.embedding_dimensions for v in vectors)

"""AWS Bedrock wrappers.

Shared file. Two independent functions, low conflict risk:
  - embed_texts  -> Dev A (Titan Embed Text v2)
  - chat         -> Dev B (Nova Pro Converse)  [placeholder below]
"""

from __future__ import annotations

import json
from functools import lru_cache

import boto3

from app import config


@lru_cache(maxsize=1)
def _client():
    return boto3.client("bedrock-runtime", region_name=config.AWS_REGION)


# ---------------------------------------------------------------------------
# Dev A — Titan Embed Text v2
# ---------------------------------------------------------------------------
def embed_text(text: str) -> list[float]:
    """Embed a single string into a 1024-dim vector via Titan Embed Text v2."""
    body = json.dumps({"inputText": text, "dimensions": config.EMBED_DIMENSIONS})
    resp = _client().invoke_model(modelId=config.BEDROCK_EMBED_MODEL_ID, body=body)
    payload = json.loads(resp["body"].read())
    return payload["embedding"]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed many strings. Titan has no batch endpoint, so loop per input."""
    return [embed_text(t) for t in texts]


# ---------------------------------------------------------------------------
# Dev B — Nova Pro Converse  (placeholder; Dev B implements)
# ---------------------------------------------------------------------------
def chat(*args, **kwargs):  # noqa: ANN002, ANN003
    raise NotImplementedError("Nova Pro Converse wrapper is owned by Dev B.")
"""Thin wrappers around AWS Bedrock: Titan Embed Text v2 and Nova Pro (Converse API).

Split by milestone ownership (see TEAM_PLAN.md Phase 0):
    - embed_text / embed_texts  -> Titan Embed wrapper (Dev A's half of Milestone 1)
    - chat_converse             -> Nova Pro wrapper (Dev B's half of Milestone 1)
Both live in one file because they share a boto3 client and are low-conflict to merge.

Run `python -m app.bedrock` for a smoke test of both calls.
"""
from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from typing import Any

import boto3
import numpy as np
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


@lru_cache(maxsize=1)
def get_bedrock_client():
    """Cached bedrock-runtime client using the explicit .env credentials.

    Built explicitly (not via boto3's default credential chain) because the .env
    keys are named ACCESS_KEY_ID/SECRET_ACCESS_KEY rather than the AWS_-prefixed
    names boto3 auto-detects.
    """
    return boto3.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
    )


class BedrockError(RuntimeError):
    """Raised when a Bedrock call fails, wrapping the underlying boto3 error."""


_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "and", "or", "but", "if", "then", "so", "that", "this", "these", "those",
    "it", "its", "to", "of", "in", "on", "at", "for", "with", "from", "by",
    "as", "what", "who", "when", "where", "why", "how", "do", "does", "did",
    "you", "your", "we", "our", "us", "they", "them", "their", "he", "she",
    "his", "her", "i", "me", "my", "tell", "please", "can", "could", "would",
    "should", "more", "about",
}


def local_hash_embed(text: str, dimensions: int | None = None) -> list[float]:
    """Deterministic offline fallback embedding (stopword-filtered hashed bag-of-words,
    L2-normalized).

    NOT semantically strong - only used when settings.embedding_backend == "local"
    (tests, or building/querying the fixture session without live AWS access) so
    retrieval/evidence-gate logic can be exercised end to end without Bedrock.
    Stable across processes because it hashes with sha256 instead of Python's
    randomized str hash. Stopwords are dropped so generic query words (what/is/your/...)
    don't produce noise similarity against unrelated chunks.
    """
    import re

    dims = dimensions or settings.embedding_dimensions
    vector = np.zeros(dims, dtype=np.float32)
    for token in re.findall(r"\w+", text.lower()):
        if token in _STOPWORDS:
            continue
        bucket = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dims
        vector[bucket] += 1.0
    norm = np.linalg.norm(vector)
    if norm > 0:
        vector = vector / norm
    return vector.tolist()


def embed_text(text: str, dimensions: int | None = None, normalize: bool = True) -> list[float]:
    """Embed a single string with Titan Embed Text v2. Returns an L2-normalized vector.

    Normalizing here (rather than trusting the API's own `normalize` flag) guarantees
    FAISS IndexFlatIP inner-product search behaves like cosine similarity end to end.
    """
    if settings.embedding_backend == "local":
        return local_hash_embed(text, dimensions)

    dims = dimensions or settings.embedding_dimensions
    body = {"inputText": text, "dimensions": dims, "normalize": normalize}
    client = get_bedrock_client()
    try:
        response = client.invoke_model(
            modelId=settings.titan_model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
    except (BotoCoreError, ClientError) as exc:
        raise BedrockError(f"Titan embed call failed: {exc}") from exc

    payload = json.loads(response["body"].read())
    vector = payload.get("embedding")
    if not vector:
        raise BedrockError(f"Titan embed response missing 'embedding': {payload}")

    arr = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr.tolist()


def embed_texts(texts: list[str], dimensions: int | None = None) -> list[list[float]]:
    """Embed multiple strings. Titan Embed v2 has no batch endpoint, so this loops."""
    return [embed_text(t, dimensions=dimensions) for t in texts]


def chat_converse(
    user_message: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_tokens: int = 512,
    temperature: float = 0.2,
    top_p: float = 0.9,
) -> str:
    """Call Nova Pro via the Bedrock Converse API and return the reply text."""
    client = get_bedrock_client()
    try:
        response: dict[str, Any] = client.converse(
            modelId=settings.nova_model_id,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={
                "maxTokens": max_tokens,
                "temperature": temperature,
                "topP": top_p,
            },
        )
    except (BotoCoreError, ClientError) as exc:
        raise BedrockError(f"Nova Pro converse call failed: {exc}") from exc

    try:
        content = response["output"]["message"]["content"]
        return "".join(block.get("text", "") for block in content).strip()
    except (KeyError, IndexError) as exc:
        raise BedrockError(f"Unexpected Nova Pro response shape: {response}") from exc


def _smoke_test() -> None:
    logging.basicConfig(level=logging.INFO)
    print("Embedding a test sentence with Titan Embed Text v2...")
    vector = embed_text("Aurora Digital Solutions offers Salesforce consulting.")
    print(f"  OK - vector length={len(vector)}, first 5 dims={vector[:5]}")

    print("Calling Nova Pro via Converse API...")
    reply = chat_converse("Reply with exactly the word: PONG")
    print(f"  OK - reply={reply!r}")


if __name__ == "__main__":
    _smoke_test()

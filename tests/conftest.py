import os

# Must run before any `from app...` import touches app.config (module-level Settings()).
# Isolates tests from the real .env / real fixture_demo / real SQLite file, and swaps in
# the deterministic offline embedding backend so no live AWS calls are needed to test
# retrieval/flow-routing logic.
os.environ.setdefault("EMBEDDING_BACKEND", "local")
os.environ.setdefault("SQLITE_PATH", "data/test_app.db")
os.environ.setdefault("SESSIONS_DIR", "data/test_sessions")
# Tuned for the crude local hash-embedding backend (short query vs longer chunk text
# yields much lower cosine similarity than real Titan embeddings do) - NOT the value
# used against real Bedrock embeddings (see .env.example / app/config.py default 0.35).
os.environ.setdefault("EVIDENCE_SIMILARITY_THRESHOLD", "0.15")

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _fixture_session():
    """Builds data/test_sessions/fixture_demo once per test run (local embeddings)."""
    from scripts.build_fixture_session import build

    build()


@pytest.fixture(scope="session", autouse=True)
def _patch_llm():
    """Nova Pro is never called in tests; replace it with a deterministic fake reply."""
    mp = pytest.MonkeyPatch()

    def _fake_chat_converse(user_message: str, system_prompt: str = "", **kwargs) -> str:
        return "Based on the provided website evidence, here is a grounded answer. [1]"

    mp.setattr("app.rag.chat_converse", _fake_chat_converse)
    yield
    mp.undo()

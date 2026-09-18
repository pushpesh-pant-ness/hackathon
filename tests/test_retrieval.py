"""Tests app/retrieval.py against the fixture_demo session (local hash-embedding backend
configured in tests/conftest.py, no live AWS calls)."""
from app import retrieval
from scripts.build_fixture_session import SESSION_ID as FIXTURE_SESSION_ID


def test_search_ranks_matching_service_first():
    results = retrieval.search(FIXTURE_SESSION_ID, "Tell me about Salesforce consulting benefits", k=5)
    assert results
    assert results[0]["service"] == "Salesforce Consulting"


def test_search_service_filter_falls_back_when_no_match():
    # A service name that matches no chunk must not zero-out results (FINAL_PLAN Sec. 19).
    results = retrieval.search(FIXTURE_SESSION_ID, "cloud migration approach", k=5, service="unknown-service")
    assert results


def test_retrieve_accepts_when_evidence_is_relevant():
    result = retrieval.retrieve(FIXTURE_SESSION_ID, "What does the data engineering service include?")
    assert result.accepted
    assert result.selected
    assert any(c["service"] == "Data Engineering" for c in result.selected)


def test_passes_evidence_gate_threshold():
    assert retrieval.passes_evidence_gate(0.5, threshold=0.35) is True
    assert retrieval.passes_evidence_gate(0.1, threshold=0.35) is False


def test_unknown_session_returns_empty_result():
    result = retrieval.retrieve("does-not-exist-session", "anything")
    assert result.accepted is False
    assert result.candidates == []

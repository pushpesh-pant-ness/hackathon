"""Tests app/flows.py routing against the evaluation set in test_questions.json,
run against the fixture_demo session (see tests/conftest.py for the offline setup).
"""
import json
from pathlib import Path

import pytest

from app import flows, sessions
from scripts.build_fixture_session import SESSION_ID as FIXTURE_SESSION_ID

QUESTIONS = json.loads((Path(__file__).parent / "test_questions.json").read_text(encoding="utf-8"))
FLOW_CASES = [q for q in QUESTIONS if "expected_flow" in q]


@pytest.mark.parametrize("case", FLOW_CASES, ids=[c["question"] for c in FLOW_CASES])
def test_expected_flow(case):
    if case.get("requires_prior_context"):
        flows.route(FIXTURE_SESSION_ID, "Tell me about Salesforce")

    result = flows.route(FIXTURE_SESSION_ID, case["question"])

    assert result["flow"] == case["expected_flow"]
    if case.get("must_have_source"):
        assert result["sources"], f"expected sources for: {case['question']}"


def test_fallback_when_evidence_is_insufficient():
    result = flows.route(FIXTURE_SESSION_ID, "What is your pricing?")
    assert result["flow"] == "fallback"
    assert result["sources"] == []


def test_fallback_for_unrelated_question():
    result = flows.route(FIXTURE_SESSION_ID, "Tell me something about dinosaurs")
    assert result["flow"] == "fallback"


def test_greeting_prefix_stripped_before_cascade():
    """"Hi, <question>" must not short-circuit into a static greeting (architecture.md 4.6.1)."""
    result = flows.route(FIXTURE_SESSION_ID, "Hi! What services do you offer?")
    assert result["flow"] == "service_list"


def test_follow_up_resolves_current_service():
    sessions.update_session_state(FIXTURE_SESSION_ID, current_service=None, last_flow=None, last_sources=[])
    flows.route(FIXTURE_SESSION_ID, "Tell me about Salesforce")

    result = flows.route(FIXTURE_SESSION_ID, "What about its benefits?")

    assert result["flow"] == "follow_up"


def test_follow_up_without_context_falls_through():
    sessions.update_session_state(FIXTURE_SESSION_ID, current_service=None, last_flow=None, last_sources=[])
    result = flows.route(FIXTURE_SESSION_ID, "tell me more")
    assert result["flow"] != "follow_up"

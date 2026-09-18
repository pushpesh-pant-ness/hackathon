"""Deterministic, rule-based conversation flow router (architecture.md Section 4.6).

First-match-wins cascade: greeting -> service_list -> service_detail -> follow_up ->
general_qa -> fallback. No LLM call decides the flow itself - only downstream RAG
generation (for the flows that need it) calls Nova Pro. This keeps every turn fast,
free to route, and fully testable (see tests/test_questions.json).
"""
from __future__ import annotations

import re
import time

from app import db, rag, sessions

GREETING_REPLY = (
    "Hello! Ask me anything about this website - services, pricing, locations, "
    "or anything else covered on the site."
)

GREETING_LEXICON = {
    "hi", "hello", "hey", "hiya", "yo", "howdy", "greetings",
    "good morning", "good afternoon", "good evening",
    "hi there", "hello there", "sup", "whats up",
}

GREETING_PREFIX_RE = re.compile(
    r"^(hi|hello|hey|hiya|yo|howdy|greetings|good morning|good afternoon|good evening)[,!.\s]+",
    re.IGNORECASE,
)

SERVICE_LIST_PATTERNS = [
    r"\bwhat services\b",
    r"\bwhat do you (offer|provide|do)\b",
    r"\bservices (do you provide|do you offer|you provide|you offer|available)\b",
    r"\blist (of )?services\b",
    r"\bwhat kind of services\b",
]

FOLLOW_UP_ANAPHORA_RE = re.compile(r"\b(it|its|that|this|those|them|more)\b")

QUESTION_WORDS = {
    "what", "where", "who", "when", "why", "how",
    "is", "are", "do", "does", "can", "could", "would", "will", "should", "tell",
}

_STOPWORDS = {"and", "the", "of", "for", "services", "service", "consulting", "solutions"}

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

FALLBACK_FLOWS = {"service_list", "service_detail", "follow_up", "general_qa"}


def _normalize(text: str) -> str:
    text = _PUNCT_RE.sub(" ", text.strip().lower())
    return _WS_RE.sub(" ", text).strip()


def _strip_greeting_prefix(message: str) -> tuple[str, bool]:
    match = GREETING_PREFIX_RE.match(message.strip())
    if not match:
        return message, False
    return message[match.end():].strip(" ,.!?"), True


def _matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def _match_known_service(normalized_message: str, services: list[str]) -> str | None:
    best_match, best_score = None, 0
    for service in services:
        service_normalized = _normalize(service)
        if service_normalized and service_normalized in normalized_message:
            return service
        words = [w for w in service_normalized.split() if w not in _STOPWORDS and len(w) >= 4]
        score = sum(1 for w in words if re.search(rf"\b{re.escape(w)}\b", normalized_message))
        if score > best_score:
            best_match, best_score = service, score
    return best_match if best_score > 0 else None


def _is_short_anaphoric(normalized_message: str) -> bool:
    words = normalized_message.split()
    return bool(words) and len(words) <= 7 and bool(FOLLOW_UP_ANAPHORA_RE.search(normalized_message))


def _is_well_formed_question(raw_message: str, normalized_message: str) -> bool:
    if raw_message.strip().endswith("?"):
        return True
    words = normalized_message.split()
    return bool(words) and words[0] in QUESTION_WORDS


def _handle_service_list(session_id: str) -> tuple[str, list[dict], bool, float]:
    services = db.list_services(session_id)
    if not services:
        result = rag.answer(session_id, "What services do you provide?")
        return result["reply"], result["sources"], result["evidence_accepted"], result["top_score"]

    reply = "We offer the following services: " + ", ".join(services) + "."
    documents = db.list_documents(session_id)
    sources, seen_urls = [], set()
    for service in services:
        doc = next((d for d in documents if d.get("service") == service and d["url"] not in seen_urls), None)
        if doc:
            sources.append({"title": doc["title"], "url": doc["url"], "chunk_id": None})
            seen_urls.add(doc["url"])
    return reply, sources, True, 1.0


def route(session_id: str, message: str) -> dict:
    """Route one user turn: decide the flow, run retrieval/generation if needed,
    persist the flow_event + assistant message, and return {reply, flow, sources}.
    """
    start = time.perf_counter()
    state = sessions.get_session_state(session_id)

    stripped, had_prefix = _strip_greeting_prefix(message)
    is_pure_greeting = _normalize(message) in GREETING_LEXICON or (had_prefix and not stripped)
    working = stripped if (had_prefix and stripped) else message
    working_normalized = _normalize(working)

    services = db.list_services(session_id)
    matched_service = None if is_pure_greeting else _match_known_service(working_normalized, services)

    if is_pure_greeting:
        matched = "greeting"
        reply, sources, answered, confidence = GREETING_REPLY, [], True, 1.0
    elif _matches_any(SERVICE_LIST_PATTERNS, working_normalized):
        matched = "service_list"
        reply, sources, answered, confidence = _handle_service_list(session_id)
    elif matched_service:
        matched = "service_detail"
        result = rag.answer(session_id, working, service=matched_service)
        reply, sources, answered, confidence = (
            result["reply"], result["sources"], result["evidence_accepted"], result["top_score"],
        )
        sessions.update_session_state(session_id, current_service=matched_service)
    elif _is_short_anaphoric(working_normalized) and (state["current_service"] or state["last_flow"]):
        matched = "follow_up"
        scope = state["current_service"]
        resolved_query = f"Regarding {scope}: {working}" if scope else working
        result = rag.answer(session_id, resolved_query, service=scope)
        reply, sources, answered, confidence = (
            result["reply"], result["sources"], result["evidence_accepted"], result["top_score"],
        )
    elif _is_well_formed_question(working, working_normalized):
        matched = "general_qa"
        result = rag.answer(session_id, working)
        reply, sources, answered, confidence = (
            result["reply"], result["sources"], result["evidence_accepted"], result["top_score"],
        )
    else:
        matched = "fallback"
        reply, sources, answered, confidence = rag.FALLBACK_MESSAGE, [], False, 0.0

    flow = matched if answered else "fallback"
    latency_ms = int((time.perf_counter() - start) * 1000)

    if flow in FALLBACK_FLOWS:
        sessions.update_session_state(session_id, last_flow=flow, last_sources=sources)

    db.insert_flow_event(session_id, flow=flow, confidence=confidence, matched=matched, answered=answered)
    db.insert_message(
        session_id,
        role="assistant",
        content=reply,
        flow=flow,
        sources=sources,
        retrieval_score=confidence,
        latency_ms=latency_ms,
    )

    return {"reply": reply, "flow": flow, "sources": sources}

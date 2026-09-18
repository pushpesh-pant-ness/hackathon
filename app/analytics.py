"""Basic flow/retrieval analytics report (architecture.md Section 9, GET /analytics/report).

Deliberately small: aggregates what's already in SQLite rather than standing up a
separate analytics store (see FINAL_PLAN_IMPROVED.md Section 26 - "the purpose is
observability and session isolation, not building a production analytics warehouse").
"""
from __future__ import annotations

from app import sessions


def report(session_id: str) -> dict:
    session = sessions.get_full_session(session_id)
    if session is None:
        raise ValueError(f"Unknown session_id: {session_id}")

    assistant_messages = [m for m in session["messages"] if m["role"] == "assistant"]
    flow_events = session["flow_events"]

    fallback_count = sum(1 for e in flow_events if e["flow"] == "fallback")
    fallback_rate = fallback_count / len(flow_events) if flow_events else 0.0

    latencies = [m["latency_ms"] for m in assistant_messages if m["latency_ms"] is not None]
    avg_latency_ms = sum(latencies) / len(latencies) if latencies else None

    scores = [m["retrieval_score"] for m in assistant_messages if m["retrieval_score"] is not None]
    avg_retrieval_score = sum(scores) / len(scores) if scores else None

    flow_counts: dict[str, int] = {}
    for event in flow_events:
        flow_counts[event["flow"]] = flow_counts.get(event["flow"], 0) + 1

    source_counts: dict[str, int] = {}
    for message in assistant_messages:
        for source in message["sources"]:
            source_counts[source["url"]] = source_counts.get(source["url"], 0) + 1
    top_sources = sorted(source_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]

    return {
        "session_id": session_id,
        "website_url": session["website_url"],
        "status": session["status"],
        "pages_discovered": session["pages_discovered"],
        "pages_retained": session["pages_retained"],
        "chunks": session["chunks"],
        "services": session["services"],
        "turn_count": len(assistant_messages),
        "fallback_rate": round(fallback_rate, 3),
        "avg_latency_ms": round(avg_latency_ms, 1) if avg_latency_ms is not None else None,
        "avg_retrieval_score": round(avg_retrieval_score, 3) if avg_retrieval_score is not None else None,
        "flow_counts": flow_counts,
        "top_sources": [{"url": url, "count": count} for url, count in top_sources],
    }

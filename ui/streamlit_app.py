"""Streamlit UI: URL input, crawl status, discovered services, chat, sources.

Talks to the FastAPI backend over HTTP only - per architecture.md Section 10, AWS
credentials stay server-side. This module deliberately avoids importing app.config
(which loads AWS keys into memory) so no Bedrock credential ever exists in this process.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
MAX_PAGES = int(os.getenv("MAX_PAGES", "20"))

st.set_page_config(page_title="Website RAG Chatbot", page_icon="🌐", layout="wide")


class BackendError(Exception):
    """Raised for any failed backend call; the message is already user-facing."""


def _error_detail(exc: requests.RequestException) -> str:
    """Prefer FastAPI's {"detail": ...} JSON body over the raw exception text."""
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        if detail:
            return str(detail)
    return str(exc)


def call_api(method: str, path: str, *, timeout: int = 15, **kwargs) -> dict:
    try:
        resp = requests.request(method, f"{BACKEND_URL}{path}", timeout=timeout, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise BackendError(_error_detail(exc)) from exc


TERMINAL_STATUSES = ("done", "failed")

_DEFAULTS = {
    "session_id": None,
    "website_url": "",
    "crawl_job_id": None,
    "crawl_status": None,
    "pages_retained": 0,
    "pages_discovered": 0,
    "pages_failed": 0,
    "crawl_error": None,
    "services": [],
    "messages": [],
    "poll_count": 0,
    "error": None,
}
for _key, _value in _DEFAULTS.items():
    st.session_state.setdefault(_key, _value)

with st.sidebar:
    st.header("Session")
    if st.session_state.session_id:
        st.caption(f"session_id: {st.session_state.session_id}")
        st.caption(f"website: {st.session_state.website_url}")
    if st.button("Start Over"):
        st.session_state.clear()
        st.rerun()

st.title("🌐 Website RAG Chatbot")
st.caption("Enter a website URL, build a bounded knowledge base, then ask questions grounded in its content.")

with st.form("ingest_form", clear_on_submit=False):
    url = st.text_input("Website URL", value=st.session_state.website_url, placeholder="https://example.com")
    submitted = st.form_submit_button("Build Knowledge Base")

if submitted and url:
    try:
        with st.spinner("Starting crawl..."):
            data = call_api("POST", "/ingest", json={"url": url})
        st.session_state.session_id = data["session_id"]
        st.session_state.crawl_job_id = data["job_id"]
        st.session_state.crawl_status = data["status"]
        st.session_state.website_url = url
        st.session_state.messages = []
        st.session_state.poll_count = 0
        st.session_state.pages_failed = 0
        st.session_state.crawl_error = None
        st.session_state.error = None
    except BackendError as exc:
        st.session_state.error = f"Could not reach backend at {BACKEND_URL}: {exc}"

if st.session_state.error:
    st.error(st.session_state.error)

# Only poll the backend while the crawl is still in flight; once it reaches a terminal
# status, render straight from cached session_state instead of re-fetching on every rerun
# (e.g. every chat turn triggers a Streamlit rerun of this whole script).
if st.session_state.crawl_job_id and st.session_state.crawl_status not in TERMINAL_STATUSES:
    try:
        status_payload = call_api("GET", f"/ingest/{st.session_state.crawl_job_id}")
        st.session_state.crawl_status = status_payload["status"]
        st.session_state.pages_retained = status_payload["pages_retained"]
        st.session_state.pages_discovered = status_payload["pages_discovered"]
        st.session_state.pages_failed = status_payload["pages_failed"]
        st.session_state.services = status_payload["services"]
        st.session_state.crawl_error = status_payload.get("error")
    except BackendError as exc:
        st.error(f"Could not fetch crawl status: {exc}")

if st.session_state.crawl_job_id:
    status = st.session_state.crawl_status
    still_working = status not in TERMINAL_STATUSES
    label = {
        "queued": "Queued...",
        "running": "Crawling website...",
        "done": "Knowledge base ready"
        if not st.session_state.pages_failed
        else "Knowledge base ready (with warnings)",
        "failed": "Crawl failed",
    }.get(status, "Working...")
    box_state = "error" if status == "failed" else ("complete" if status == "done" else "running")

    with st.status(label, state=box_state, expanded=still_working or status == "failed"):
        progress = min(st.session_state.pages_retained / MAX_PAGES, 1.0) if MAX_PAGES else 0.0
        st.progress(progress, text=f"{st.session_state.pages_retained} / {MAX_PAGES} pages retained")

        if status == "failed":
            st.error(
                "Knowledge base could not be created. Please check the website URL or try another site.\n\n"
                f"({st.session_state.crawl_error or 'unknown error'})"
            )
        elif status == "done" and st.session_state.pages_failed:
            st.warning(
                "Crawl completed with warnings\n\n"
                f"Pages retained: {st.session_state.pages_retained}  \n"
                f"Pages failed: {st.session_state.pages_failed}"
            )
        elif status == "done":
            st.success("Knowledge base ready.")

    if still_working:
        st.session_state.poll_count += 1
        if st.session_state.poll_count <= 15:
            time.sleep(1.5)
            st.rerun()
        else:
            st.button("Refresh status")

    if st.session_state.services:
        st.subheader("Discovered Services")
        for service in st.session_state.services:
            st.markdown(f"- {service}")
    elif status == "done":
        st.caption("No distinct services were detected on this site.")

st.divider()
st.subheader("Chat")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])
        if msg.get("sources"):
            links = " ".join(f"[{s['title']}]({s['url']})" for s in msg["sources"])
            st.caption(f"Sources: {links}")

chat_disabled = st.session_state.crawl_status != "done"
prompt = st.chat_input(
    "Ask a question about this website..." if not chat_disabled else "Build a knowledge base first",
    disabled=chat_disabled,
)

if prompt:
    with st.chat_message("user"):
        st.write(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt, "sources": []})

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                data = call_api(
                    "POST",
                    "/chat",
                    json={"session_id": st.session_state.session_id, "message": prompt},
                    timeout=30,
                )
                reply, sources = data["reply"], data.get("sources", [])
            except BackendError as exc:
                reply, sources = f"Error contacting backend: {exc}", []
        st.write(reply)
        if sources:
            links = " ".join(f"[{s['title']}]({s['url']})" for s in sources)
            st.caption(f"Sources: {links}")

    st.session_state.messages.append({"role": "assistant", "content": reply, "sources": sources})
    st.rerun()

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


# Ingestion is a 3-phase flow: crawl -> site map, then build -> knowledge base,
# then chat is unlocked. Poll while a background job is actively working; render
# from cached session_state once it lands on a stable status.
POLL_STATUSES = {"queued", "crawling", "building"}

_DEFAULTS = {
    "session_id": None,
    "website_url": "",
    "crawl_job_id": None,
    "crawl_status": None,
    "pages_retained": 0,
    "pages_discovered": 0,
    "pages_failed": 0,
    "chunks": 0,
    "crawl_error": None,
    "services": [],
    "sitemap_pages": [],
    "sitemap_failures": [],
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
    submitted = st.form_submit_button("Build Site Map")

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
        st.session_state.chunks = 0
        st.session_state.services = []
        st.session_state.sitemap_pages = []
        st.session_state.sitemap_failures = []
        st.session_state.crawl_error = None
        st.session_state.error = None
    except BackendError as exc:
        st.session_state.error = f"Could not reach backend at {BACKEND_URL}: {exc}"

if st.session_state.error:
    st.error(st.session_state.error)

# Only poll the backend while a job is actively running; once it lands on a stable
# status (sitemap_ready/done/failed), render from cached session_state instead of
# re-fetching on every rerun (e.g. every chat turn triggers a Streamlit rerun of the
# whole script).
if st.session_state.crawl_job_id and st.session_state.crawl_status in POLL_STATUSES:
    try:
        status_payload = call_api("GET", f"/ingest/{st.session_state.crawl_job_id}")
        st.session_state.crawl_status = status_payload["status"]
        st.session_state.pages_retained = status_payload["pages_retained"]
        st.session_state.pages_discovered = status_payload["pages_discovered"]
        st.session_state.pages_failed = status_payload["pages_failed"]
        st.session_state.services = status_payload["services"]
        st.session_state.chunks = status_payload["chunks"]
        st.session_state.crawl_error = status_payload.get("error")
        sitemap = status_payload.get("sitemap") or {}
        st.session_state.sitemap_pages = sitemap.get("pages", [])
        st.session_state.sitemap_failures = sitemap.get("failures", [])
    except BackendError as exc:
        st.error(f"Could not fetch status: {exc}")

if st.session_state.crawl_job_id:
    status = st.session_state.crawl_status
    crawling = status in ("queued", "crawling")
    # A site map only fails to appear if the crawl itself failed outright; once
    # sitemap_ready is reached, any later "failed" belongs to the build phase instead.
    crawl_failed = status == "failed" and not st.session_state.sitemap_pages

    sitemap_label = {
        "queued": "Queued...",
        "crawling": "Building site map...",
    }.get(status, "Site map failed" if crawl_failed else "Site map ready")
    sitemap_state = "running" if crawling else ("error" if crawl_failed else "complete")

    with st.status(sitemap_label, state=sitemap_state, expanded=crawling or crawl_failed):
        progress = min(st.session_state.pages_retained / MAX_PAGES, 1.0) if MAX_PAGES else 0.0
        st.progress(progress, text=f"{st.session_state.pages_retained} pages found")

        if crawl_failed:
            st.error(
                "Could not build a site map. Please check the website URL or try another site.\n\n"
                f"({st.session_state.crawl_error or 'unknown error'})"
            )
        elif not crawling:
            st.success(
                f"Found {st.session_state.pages_retained} page(s)"
                + (f", {st.session_state.pages_failed} skipped" if st.session_state.pages_failed else "")
                + "."
            )
            if st.session_state.sitemap_pages:
                with st.expander(f"Site map ({len(st.session_state.sitemap_pages)} pages)"):
                    for page in st.session_state.sitemap_pages:
                        st.markdown(f"- {page['canonical_url']}")
            if st.session_state.sitemap_failures:
                with st.expander(f"Pages skipped ({len(st.session_state.sitemap_failures)})"):
                    for failure in st.session_state.sitemap_failures:
                        st.caption(f"{failure['url']} — {failure['reason']}")

    if status == "sitemap_ready":
        if st.button("Add to Knowledge Base", type="primary"):
            try:
                build_data = call_api("POST", f"/ingest/{st.session_state.crawl_job_id}/build")
                st.session_state.crawl_status = build_data["status"]
                st.session_state.poll_count = 0
                st.rerun()
            except BackendError as exc:
                st.error(f"Could not start knowledge base build: {exc}")

    build_reachable = status in ("building", "done") or (status == "failed" and st.session_state.sitemap_pages)
    if build_reachable:
        build_label = {
            "building": "Adding to knowledge base...",
            "done": "Knowledge base ready"
            if not st.session_state.pages_failed
            else "Knowledge base ready (with warnings)",
            "failed": "Knowledge base build failed",
        }[status]
        build_state = "running" if status == "building" else ("error" if status == "failed" else "complete")

        with st.status(build_label, state=build_state, expanded=status in ("building", "failed")):
            if status == "building":
                st.write("Cleaning pages, detecting services, chunking, embedding, and indexing...")
            elif status == "failed":
                st.error(
                    "Knowledge base could not be built from the site map.\n\n"
                    f"({st.session_state.crawl_error or 'unknown error'})"
                )
            elif st.session_state.pages_failed:
                st.warning(
                    "Knowledge base built with warnings\n\n"
                    f"Chunks indexed: {st.session_state.chunks}  \n"
                    f"Pages skipped during crawl: {st.session_state.pages_failed}"
                )
            else:
                st.success(f"Indexed {st.session_state.chunks} chunks. Ready for questions.")

        if st.session_state.services:
            st.subheader("Discovered Services")
            for service in st.session_state.services:
                st.markdown(f"- {service}")
        elif status == "done":
            st.caption("No distinct services were detected on this site.")

    if status in POLL_STATUSES:
        st.session_state.poll_count += 1
        if st.session_state.poll_count <= 30:
            time.sleep(1.5)
            st.rerun()
        else:
            st.button("Refresh status")

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

# Streamlit UI is owned by Dev B (Milestone 7). Placeholder to reserve the folder.
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

_DEFAULTS = {
    "session_id": None,
    "website_url": "",
    "crawl_job_id": None,
    "crawl_status": None,
    "pages_retained": 0,
    "pages_discovered": 0,
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
        resp = requests.post(f"{BACKEND_URL}/ingest", json={"url": url}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        st.session_state.session_id = data["session_id"]
        st.session_state.crawl_job_id = data["job_id"]
        st.session_state.crawl_status = data["status"]
        st.session_state.website_url = url
        st.session_state.messages = []
        st.session_state.poll_count = 0
        st.session_state.error = None
    except requests.RequestException as exc:
        st.session_state.error = f"Could not reach backend at {BACKEND_URL}: {exc}"

if st.session_state.error:
    st.error(st.session_state.error)

if st.session_state.crawl_job_id:
    st.subheader("Crawl Status")
    try:
        resp = requests.get(f"{BACKEND_URL}/ingest/{st.session_state.crawl_job_id}", timeout=10)
        resp.raise_for_status()
        status = resp.json()
        st.session_state.crawl_status = status["status"]
        st.session_state.pages_retained = status["pages_retained"]
        st.session_state.pages_discovered = status["pages_discovered"]
        st.session_state.services = status["services"]

        progress = min(status["pages_retained"] / MAX_PAGES, 1.0) if MAX_PAGES else 0.0
        st.progress(progress, text=f"{status['status']} - {status['pages_retained']} / {MAX_PAGES} pages")

        if status["status"] == "failed":
            st.error(
                "Knowledge base could not be created. "
                f"Please check the website URL or try another site. ({status.get('error') or 'unknown error'})"
            )
        elif status["status"] != "done":
            st.button("Refresh status")
            st.session_state.poll_count += 1
            if st.session_state.poll_count <= 15:
                time.sleep(1.5)
                st.rerun()
        else:
            st.success("Knowledge base ready.")
    except requests.RequestException as exc:
        st.error(f"Could not fetch crawl status: {exc}")

    if st.session_state.services:
        st.subheader("Discovered Services")
        for service in st.session_state.services:
            st.markdown(f"- {service}")

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
    st.session_state.messages.append({"role": "user", "content": prompt, "sources": []})
    try:
        resp = requests.post(
            f"{BACKEND_URL}/chat",
            json={"session_id": st.session_state.session_id, "message": prompt},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        st.session_state.messages.append(
            {"role": "assistant", "content": data["reply"], "sources": data.get("sources", [])}
        )
    except requests.RequestException as exc:
        st.session_state.messages.append(
            {"role": "assistant", "content": f"Error contacting backend: {exc}", "sources": []}
        )
    st.rerun()

"""Builds data/sessions/fixture_demo/: a tiny hand-authored "crawled" session so Dev B
can build/test retrieval, flows, RAG, and the UI without waiting on Dev A's real
crawler (see TEAM_PLAN.md Section 2, "The Key Trick").

All page content below is original placeholder copy for a fictional company
("Aurora Digital Solutions") - not copied from any real website.

Usage:
    python -m scripts.build_fixture_session
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

import faiss
import numpy as np

from app import db
from app.bedrock import embed_text
from app.config import settings

SESSION_ID = "fixture_demo"
BASE_URL = "https://aurora-digital.example"


class Page(TypedDict):
    url: str
    title: str
    service: str | None
    sections: list[tuple[str, str]]  # (heading, paragraph_text)


PAGES: list[Page] = [
    {
        "url": f"{BASE_URL}/",
        "title": "Aurora Digital Solutions — Home",
        "service": None,
        "sections": [
            (
                "Aurora Digital Solutions",
                "Aurora Digital Solutions is a technology consultancy that helps mid-size "
                "and enterprise organizations modernize how they sell, build, and operate. "
                "We partner with clients from initial strategy through hands-on delivery.",
            ),
            (
                "What We Do",
                "We offer four core service areas: Salesforce Consulting, Data Engineering, "
                "Cloud Migration, and AI & Machine Learning. Each engagement starts with a "
                "short discovery phase so recommendations are grounded in the client's "
                "actual systems and goals rather than generic best practice.",
            ),
        ],
    },
    {
        "url": f"{BASE_URL}/services",
        "title": "Our Services",
        "service": None,
        "sections": [
            (
                "Our Services",
                "Aurora Digital Solutions provides Salesforce Consulting, Data Engineering, "
                "Cloud Migration, and AI & Machine Learning services. Explore each service "
                "for details on our approach and the benefits clients typically see.",
            ),
        ],
    },
    {
        "url": f"{BASE_URL}/services/salesforce",
        "title": "Salesforce Consulting Services",
        "service": "Salesforce Consulting",
        "sections": [
            (
                "Salesforce Consulting",
                "Our Salesforce Consulting practice helps organizations implement, "
                "customize, and maintain Salesforce CRM, including Sales Cloud, Service "
                "Cloud, and Experience Cloud deployments.",
            ),
            (
                "Our Approach",
                "We begin with a workshop to map current sales and service processes, "
                "then configure Salesforce around those workflows instead of forcing a "
                "one-size-fits-all template. Every engagement includes admin training so "
                "internal teams can maintain the org after go-live.",
            ),
            (
                "Benefits",
                "Clients typically see faster deal cycles, better pipeline visibility, and "
                "fewer manual handoffs between sales and support teams after adopting our "
                "Salesforce configuration and automation recommendations.",
            ),
        ],
    },
    {
        "url": f"{BASE_URL}/services/data-engineering",
        "title": "Data Engineering Services",
        "service": "Data Engineering",
        "sections": [
            (
                "Data Engineering",
                "Our Data Engineering team designs and builds data pipelines, warehouses, "
                "and analytics-ready datasets so client teams can trust the numbers behind "
                "their decisions.",
            ),
            (
                "Our Approach",
                "We audit existing data sources, design a warehouse schema, and build "
                "incremental ETL/ELT pipelines with monitoring and data-quality checks "
                "built in from day one.",
            ),
            (
                "Benefits",
                "Clients typically reduce manual reporting effort, catch data-quality "
                "issues earlier, and unlock self-service analytics for non-technical "
                "stakeholders.",
            ),
        ],
    },
    {
        "url": f"{BASE_URL}/services/cloud-migration",
        "title": "Cloud Migration Services",
        "service": "Cloud Migration",
        "sections": [
            (
                "Cloud Migration",
                "Our Cloud Migration service moves legacy on-premises systems to AWS or "
                "Azure with minimal disruption to day-to-day operations.",
            ),
            (
                "Our Approach",
                "We inventory existing workloads, sequence migrations by risk and "
                "dependency, and run parallel environments during cutover so rollback is "
                "always possible.",
            ),
            (
                "Benefits",
                "Clients typically reduce infrastructure costs, improve system uptime, "
                "and gain elastic capacity for seasonal or unpredictable demand.",
            ),
        ],
    },
    {
        "url": f"{BASE_URL}/services/ai-ml",
        "title": "AI & Machine Learning Services",
        "service": "AI & Machine Learning",
        "sections": [
            (
                "AI & Machine Learning",
                "Our AI & Machine Learning team builds predictive models, natural "
                "language processing tools, and computer vision systems tailored to a "
                "client's existing data and infrastructure.",
            ),
            (
                "Our Approach",
                "We start from a well-defined business question, validate that available "
                "data can support it, and ship an initial model quickly so stakeholders can "
                "give feedback before a larger investment.",
            ),
            (
                "Benefits",
                "Clients typically automate repetitive judgment calls, surface insights "
                "that were previously invisible in raw data, and reduce time spent on "
                "manual analysis.",
            ),
        ],
    },
    {
        "url": f"{BASE_URL}/about",
        "title": "About Aurora Digital Solutions",
        "service": None,
        "sections": [
            (
                "About Us",
                "Aurora Digital Solutions was founded in 2015 by a small group of "
                "engineers and consultants who wanted to combine deep technical delivery "
                "with pragmatic, business-first advice.",
            ),
            (
                "Our Values",
                "We value transparency with clients, measurable outcomes over vanity "
                "metrics, and leaving every client's internal team more capable than we "
                "found them.",
            ),
        ],
    },
    {
        "url": f"{BASE_URL}/contact",
        "title": "Contact Us",
        "service": None,
        "sections": [
            (
                "Our Offices",
                "Aurora Digital Solutions has offices in Austin, Texas and Bengaluru, "
                "India. Our Austin office covers the Americas, and our Bengaluru office "
                "covers APAC and EMEA client engagements.",
            ),
            (
                "Contact Us",
                "Reach out through the contact form on this page to schedule an initial "
                "discovery call with our team.",
            ),
        ],
    },
]


def _build_chunks() -> list[dict]:
    chunks = []
    chunk_index_by_doc: dict[str, int] = {}
    for page in PAGES:
        document_id = page["url"].rsplit("/", 1)[-1] or "home"
        headings = [heading for heading, _ in page["sections"]]
        for heading, text in page["sections"]:
            idx = chunk_index_by_doc.get(document_id, 0)
            chunk_index_by_doc[document_id] = idx + 1
            chunks.append(
                {
                    "chunk_id": f"{document_id}-{idx}",
                    "document_id": document_id,
                    "chunk_index": idx,
                    "text": f"{page['title']} > {heading}\n\n{text}",
                    "source_url": page["url"],
                    "title": page["title"],
                    "heading": heading,
                    "service": page["service"],
                    "crawl_session_id": SESSION_ID,
                }
            )
    return chunks


def build() -> None:
    print(f"Embedding backend: {settings.embedding_backend}")
    chunks = _build_chunks()
    print(f"Built {len(chunks)} chunks from {len(PAGES)} fixture pages. Embedding...")

    vectors = [embed_text(c["text"]) for c in chunks]
    matrix = np.asarray(vectors, dtype=np.float32)

    index = faiss.IndexFlatIP(settings.embedding_dimensions)
    index.add(matrix)

    session_dir = settings.session_dir(SESSION_ID)
    session_dir.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(session_dir / "index.faiss"))
    (session_dir / "chunks.json").write_text(json.dumps(chunks, indent=2), encoding="utf-8")

    services = sorted({p["service"] for p in PAGES if p["service"]})
    documents = [
        {
            "document_id": p["url"].rsplit("/", 1)[-1] or "home",
            "source_url": p["url"],
            "canonical_url": p["url"],
            "title": p["title"],
            "headings": [h for h, _ in p["sections"]],
            "service": p["service"],
        }
        for p in PAGES
    ]
    manifest = {
        "session_id": SESSION_ID,
        "base_url": BASE_URL,
        "normalized_host": "aurora-digital.example",
        "pages_discovered": len(PAGES),
        "pages_retained": len(PAGES),
        "pages_failed": 0,
        "chunks": len(chunks),
        "services": services,
        "embedding_model": (
            settings.titan_model_id if settings.embedding_backend == "bedrock" else "local-hash-embed"
        ),
        "embedding_dimensions": settings.embedding_dimensions,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "ready",
        "documents": documents,
    }
    (session_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    db.init_db()
    db.create_session(BASE_URL, normalized_host="aurora-digital.example", session_id=SESSION_ID)
    for doc in documents:
        db.insert_document(SESSION_ID, url=doc["source_url"], title=doc["title"], service=doc["service"])
    db.update_session(
        SESSION_ID,
        status="ready",
        pages_discovered=len(PAGES),
        pages_retained=len(PAGES),
        chunks=len(chunks),
    )

    print(f"Wrote fixture session to {session_dir}")
    print(f"Services: {services}")


if __name__ == "__main__":
    build()

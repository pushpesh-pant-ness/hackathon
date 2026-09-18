"""Build the `fixture_demo` session (Dev A — Phase 0).

Creates a tiny, self-contained knowledge base so Dev B can build retrieval,
the evidence gate, the flow router and the UI shell before the real crawler
exists. The chunk/manifest shapes here ARE the data contract (§13, §27).

Usage:
    python -m ingest.build_fixture              # deterministic offline embeddings
    python -m ingest.build_fixture --bedrock    # real Titan embeddings (needs AWS)

Output: data/sessions/fixture_demo/{raw/,cleaned/,index.faiss,chunks.json,manifest.json}
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone

import numpy as np

from app import config
from app.vectorstore import build_index

SESSION_ID = "fixture_demo"
BASE_URL = "https://acme-cloud.example.com"
HOST = "acme-cloud.example.com"


# --- Fake source pages: (path, title, service, [(heading, body), ...]) ---
PAGES = [
    (
        "/",
        "Acme Cloud — Home",
        None,
        [
            ("Acme Cloud", "Acme Cloud helps mid-size companies move to the cloud with confidence. We offer managed migration, data engineering, and Salesforce consulting services."),
            ("Why Acme", "Our teams have delivered over 200 cloud projects. We focus on reliability, cost control, and measurable business outcomes."),
        ],
    ),
    (
        "/services/salesforce",
        "Salesforce Consulting — Acme Cloud",
        "Salesforce",
        [
            ("Salesforce Consulting", "Acme Cloud is a certified Salesforce partner. We implement Sales Cloud, Service Cloud, and custom Lightning applications tailored to your sales process."),
            ("Our Approach", "We start with a discovery workshop, define a rollout roadmap, then deliver in two-week sprints with continuous user training."),
            ("Benefits", "Customers typically see faster onboarding, cleaner pipeline data, and higher adoption after our Salesforce engagements."),
        ],
    ),
    (
        "/services/data-engineering",
        "Data Engineering — Acme Cloud",
        "Data Engineering",
        [
            ("Data Engineering", "We build reliable data pipelines on AWS using Glue, Redshift, and S3. Our engineers design ingestion, transformation, and warehousing layers."),
            ("Use Cases", "Common projects include migrating on-prem warehouses to Redshift, building near-real-time analytics, and setting up data quality monitoring."),
        ],
    ),
    (
        "/contact",
        "Contact — Acme Cloud",
        None,
        [
            ("Contact Us", "Reach the Acme Cloud team at hello@acme-cloud.example.com or call +1-555-0100. Our offices are in Boston and Austin."),
        ],
    ),
]


def _deterministic_embedding(text: str, dims: int) -> list[float]:
    """Reproducible pseudo-embedding for offline fixture builds.

    Not semantically meaningful; only guarantees correct shape/normalization so
    Dev B can wire retrieval without live Bedrock access.
    """
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    return rng.standard_normal(dims).astype("float32").tolist()


def build(use_bedrock: bool) -> None:
    if use_bedrock:
        from app import bedrock  # local import so offline runs need no boto3 creds

        embed = lambda texts: bedrock.embed_texts(texts)  # noqa: E731
    else:
        dims = config.EMBED_DIMENSIONS
        embed = lambda texts: [_deterministic_embedding(t, dims) for t in texts]  # noqa: E731

    now = datetime.now(timezone.utc).isoformat()
    out = config.session_dir(SESSION_ID)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "cleaned").mkdir(parents=True, exist_ok=True)

    chunks: list[dict] = []
    services: set[str] = set()

    for page_i, (path, title, service, sections) in enumerate(PAGES):
        url = f"{BASE_URL}{path}"
        document_id = f"doc-{page_i}"
        headings = [h for h, _ in sections]
        if service:
            services.add(service)

        # Persist raw + cleaned representations (mirrors real pipeline output).
        raw_html = f"<html><head><title>{title}</title></head><body>" + "".join(
            f"<h2>{h}</h2><p>{b}</p>" for h, b in sections
        ) + "</body></html>"
        cleaned_text = f"{title}\n\n" + "\n\n".join(f"{h}\n{b}" for h, b in sections)
        (out / "raw" / f"{document_id}.html").write_text(raw_html, encoding="utf-8")
        (out / "cleaned" / f"{document_id}.txt").write_text(cleaned_text, encoding="utf-8")

        for chunk_i, (heading, body) in enumerate(sections):
            chunks.append({
                "chunk_id": f"{document_id}-c{chunk_i}",
                "document_id": document_id,
                "chunk_index": chunk_i,
                "text": f"{title} > {heading}\n\n{body}",
                "source_url": url,
                "canonical_url": url,
                "title": title,
                "heading": heading,
                "service": service,
                "crawl_session_id": SESSION_ID,
            })

    embeddings = embed([c["text"] for c in chunks])

    manifest = {
        "session_id": SESSION_ID,
        "base_url": BASE_URL,
        "normalized_host": HOST,
        "pages_discovered": len(PAGES),
        "pages_retained": len(PAGES),
        "pages_failed": 0,
        "chunks": len(chunks),
        "services": sorted(services),
        "embedding_model": config.BEDROCK_EMBED_MODEL_ID if use_bedrock else "deterministic-fixture",
        "embedding_dimensions": config.EMBED_DIMENSIONS,
        "created_at": now,
        "status": "ready",
    }

    build_index(SESSION_ID, chunks, embeddings, manifest)
    print(f"Built fixture_demo: {len(PAGES)} pages, {len(chunks)} chunks -> {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bedrock", action="store_true", help="Use real Titan embeddings")
    build(use_bedrock=parser.parse_args().bedrock)

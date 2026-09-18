"""Tests for content processing: cleaning, service detection, chunking
(Dev A — Milestone 3)."""

from __future__ import annotations

from ingest.chunker import chunk_document
from ingest.crawler import RetainedPage
from ingest.extractor import CleanedDocument, clean_page
from ingest.service_detector import detect_services

SESSION = "test_session"

_SALESFORCE_HTML = """
<html>
  <head><title>Salesforce Consulting — Acme</title></head>
  <body>
    <nav><a href="/">Home</a><a href="/contact">Contact</a></nav>
    <script>var x = 1;</script>
    <main>
      <h1>Salesforce Consulting</h1>
      <p>Acme is a certified Salesforce partner delivering Sales Cloud.</p>
      <h2>Our Approach</h2>
      <p>We start with a discovery workshop and deliver in sprints.</p>
      <h2>Benefits</h2>
      <p>Customers see faster onboarding and cleaner pipeline data.</p>
    </main>
    <footer>Copyright Acme</footer>
  </body>
</html>
"""


def _page(url: str, html: str) -> RetainedPage:
    return RetainedPage(
        url=url, canonical_url=url, status_code=200,
        content_type="text/html", html=html, depth=1,
    )


def test_clean_page_extracts_title_headings_sections():
    doc = clean_page(_page("https://acme.com/services/salesforce", _SALESFORCE_HTML), SESSION)
    assert doc.title == "Salesforce Consulting — Acme"
    assert doc.headings == ["Salesforce Consulting", "Our Approach", "Benefits"]
    assert len(doc.sections) == 3
    # Boilerplate removed.
    assert "Copyright" not in doc.text
    assert "var x" not in doc.text
    assert "Contact" not in doc.text
    assert doc.document_id.startswith("doc-")


def test_clean_page_document_id_is_stable():
    p = _page("https://acme.com/services/salesforce", _SALESFORCE_HTML)
    assert clean_page(p, SESSION).document_id == clean_page(p, SESSION).document_id


def test_detect_services_labels_and_catalogs():
    docs = [
        clean_page(_page("https://acme.com/services/salesforce", _SALESFORCE_HTML), SESSION),
        clean_page(_page("https://acme.com/services/data-engineering", _SALESFORCE_HTML), SESSION),
        clean_page(_page("https://acme.com/contact", _SALESFORCE_HTML), SESSION),
    ]
    catalog = detect_services(docs)
    names = [c["name"] for c in catalog]
    assert names == ["Data Engineering", "Salesforce"]
    assert docs[0].service == "Salesforce"
    assert docs[2].service is None


def test_chunk_document_metadata_and_heading_context():
    doc = clean_page(_page("https://acme.com/services/salesforce", _SALESFORCE_HTML), SESSION)
    doc.service = "Salesforce"
    chunks = chunk_document(doc)
    assert len(chunks) == 3
    first = chunks[0]
    assert set(first) == {
        "chunk_id", "document_id", "chunk_index", "text", "source_url",
        "canonical_url", "title", "heading", "service", "crawl_session_id",
    }
    assert first["text"].startswith("Salesforce Consulting — Acme > Salesforce Consulting")
    assert first["service"] == "Salesforce"
    assert first["crawl_session_id"] == SESSION
    assert [c["chunk_index"] for c in chunks] == [0, 1, 2]


def test_chunk_splits_large_section_with_overlap():
    words = " ".join(f"w{i}" for i in range(2000))
    doc = CleanedDocument(
        document_id="doc-x", source_url="https://acme.com/x", canonical_url="https://acme.com/x",
        title="Big", headings=["Big"], sections=[{"heading": "Big", "text": words}],
        text=words, crawl_session_id=SESSION, scraped_at="now", service=None,
    )
    chunks = chunk_document(doc, target_tokens=200, overlap_tokens=40)
    assert len(chunks) > 1
    # Overlap: the tail of chunk 0 reappears at the head of chunk 1's body.
    body0 = chunks[0]["text"].split("\n\n", 1)[1].split()
    body1 = chunks[1]["text"].split("\n\n", 1)[1].split()
    assert body0[-30:] == body1[:30]

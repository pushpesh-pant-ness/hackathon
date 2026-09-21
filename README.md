# 🌐 Dynamic Website RAG Chatbot

Enter any website URL, crawl a bounded set of its pages, and chat with a grounded,
citation-backed assistant that only answers from what it actually found on that site.

Two pipelines share a common session boundary:

- **Ingestion** — URL → validate/SSRF-check → same-domain BFS crawl (≤ 20 pages) → clean HTML →
  detect services → heading-aware chunking → Titan embeddings → session-scoped FAISS index.
- **Conversation** — chat message → deterministic flow routing → evidence-gated retrieval →
  grounded Nova Pro answer → inline citations.

Full design detail lives in [architecture.md](architecture.md) (DFDs, sequence diagrams, data
model) and [FINAL_PLAN_IMPROVED.md](FINAL_PLAN_IMPROVED.md) (the original product plan).

## Tech stack

| Layer | Choice |
|---|---|
| UI | Streamlit |
| Backend | FastAPI |
| Chat model | Amazon Nova Pro (`amazon.nova-pro-v1:0`) via Bedrock Converse API |
| Embeddings | Amazon Titan Embed Text v2 (`amazon.titan-embed-text-v2:0`) |
| Vector store | FAISS `IndexFlatIP`, one index per crawl session |
| Relational store | SQLite (sessions, crawl_jobs, documents, messages, flow_events) |
| Scraper | `requests` + `BeautifulSoup` (static HTML only) |

## Project structure

```text
app/            FastAPI backend: config, Bedrock client, DB, sessions, retrieval, RAG,
                flow router, analytics, main API
ingest/         Crawl pipeline: URL validation/SSRF checks, robots.txt, canonicalizer,
                crawler (BFS), HTML cleaning, service detection, chunking, orchestration
ui/             Streamlit front end (talks to the backend over HTTP only)
scripts/        `build_fixture_session.py` — builds a canned demo session for local testing
tests/          pytest suite (offline, deterministic local embeddings, no AWS calls)
data/sessions/  One folder per crawl session: raw/, cleaned/, index.faiss, chunks.json,
                manifest.json (gitignored except the `fixture_demo` sample)
```

## Prerequisites

- Python 3.10+ (developed/tested with 3.14)
- An AWS account with **Bedrock model access enabled** for `amazon.nova-pro-v1:0` and
  `amazon.titan-embed-text-v2:0` in your chosen region, plus an IAM user/role credential
  with `bedrock:InvokeModel` and `bedrock:Converse` permissions

## Setup

```powershell
# from the repo root
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# optional: enables the headless-browser fallback for JS-rendered pages (RENDER_JS=true
# by default). Skip this and the crawler still works, just without that fallback.
playwright install chromium

copy .env.example .env
# then edit .env and fill in ACCESS_KEY_ID / SECRET_ACCESS_KEY / AWS_REGION
```

Key `.env` settings (see `.env.example` for the full list with defaults):

| Variable | Purpose |
|---|---|
| `ACCESS_KEY_ID` / `SECRET_ACCESS_KEY` / `AWS_REGION` | AWS credentials used server-side only (never sent to the UI) |
| `EMBEDDING_BACKEND` | `bedrock` (real Titan calls) or `local` (deterministic offline hash-embedding, no AWS needed) |
| `EVIDENCE_SIMILARITY_THRESHOLD` | Minimum top-score before an answer is generated instead of a fallback |
| `MAX_PAGES` / `MAX_CRAWL_DEPTH` | Crawl bounds |
| `RENDER_JS` / `JS_RENDER_TIMEOUT_MS` | Headless-browser fallback for JS-rendered pages (needs `playwright install chromium`) |
| `SESSIONS_DIR` / `SQLITE_PATH` | Storage locations |
| `BACKEND_URL` | Used by the Streamlit app to reach the FastAPI backend |

## Running it

Start the backend and UI in two separate terminals (from the repo root, with the venv active):

```powershell
uvicorn app.main:app --reload
```

```powershell
streamlit run ui/streamlit_app.py
```

Then open the Streamlit URL it prints, enter a website (e.g. `https://example.com`), wait for
the knowledge base to build, and start chatting.

## Running the tests

```powershell
python -m pytest -q
```

The suite is fully offline: `tests/conftest.py` forces `EMBEDDING_BACKEND=local`, points
`SQLITE_PATH`/`SESSIONS_DIR` at throwaway `data/test_*` locations, builds a fixture session
once per run, and monkeypatches Nova Pro so no live AWS calls happen. Two tests in
`tests/test_bedrock_embed.py` are real-AWS smoke tests and auto-skip unless credentials are
configured.

Optional manual smoke tests (make real Bedrock calls):

```powershell
python -m app.bedrock                    # embeds a sentence + calls Nova Pro
python -m scripts.build_fixture_session  # (re)builds data/sessions/fixture_demo
```

## API summary

| Endpoint | Purpose |
|---|---|
| `POST /ingest` | `{url}` → starts a background crawl, returns `{session_id, job_id, status}` |
| `GET /ingest/{job_id}` | Poll crawl progress: status, pages discovered/retained/failed, chunks, services |
| `POST /chat` | `{session_id, message}` → `{reply, flow, sources}` |
| `GET /sessions/{session_id}` | Full session state, transcript, and flow events |
| `GET /analytics/report?session_id=` | Fallback rate, latency, top sources, flow counts |

## How answers are grounded

1. **Flow routing** (`app/flows.py`) is a deterministic, first-match-wins cascade —
   `greeting → service_list → service_detail → follow_up → general_qa → fallback` — so most
   turns never touch the LLM at all (see [architecture.md §4.6](architecture.md#46-intent--flow-routing--decision-cascade)).
2. **Retrieval** (`app/retrieval.py`) embeds the query with Titan, searches the session's FAISS
   index, and optionally soft-filters by detected service (falls back to unfiltered results if
   the filter would return nothing).
3. **Evidence gate**: if the top similarity score is below `EVIDENCE_SIMILARITY_THRESHOLD`, the
   system returns a safe "I couldn't find that" reply instead of calling the LLM.
4. **Grounded generation** (`app/rag.py`) sends only the retrieved chunks to Nova Pro, marks
   them as untrusted reference data (prompt-injection defense), and requires inline `[n]`
   citations that are mapped back to source URLs.

## Security notes

- **SSRF protection**: scheme allow-list, exact host allow-listing (no `endswith` matching),
  DNS resolution checked against private/loopback/link-local/reserved ranges, and every
  redirect hop is independently re-validated.
- **robots.txt** is checked before every fetch; crawl is bounded by page count, depth, request
  timeout, and response size.
- **Secrets**: AWS credentials are read only by the FastAPI process; `ui/streamlit_app.py`
  deliberately never imports `app.config`, so no credential ever loads into the Streamlit
  process. `.env` is gitignored.
- **Known limitation**: host/IP validation happens once before the HTTP request is issued; it
  does not pin the connection to the validated IP, so a theoretical DNS-rebinding race
  (validated IP changes between check and connect) is not fully closed. Acceptable for a
  hackathon MVP crawling user-supplied public sites; would need connection-level IP pinning
  to fully close for a hardened deployment.

## Known limitations

- Single-flow-per-turn routing: a message with multiple intents (e.g. "Hi, what services do you
  offer, and do you do Salesforce work?") only answers the highest-priority clause this turn;
  the rest naturally resolves as a `follow_up` on the next turn (see
  [architecture.md §4.6.1](architecture.md#461-compound--multi-intent-messages)).
- Static HTML only by default — pages whose main content needs JavaScript to render are
  handled by an optional headless-Chromium fallback (`RENDER_JS=true` by default; only
  kicks in when the static fetch comes back empty/near-empty, and needs
  `playwright install chromium` — see Setup). Without that browser installed, such pages
  are silently skipped exactly as before. The fallback also only re-validates the page's
  *final* URL against the crawl's allowed hosts, not every subresource request the page's
  own JS makes (e.g. XHR/fetch to third-party CDNs/APIs are left alone) — acceptable for a
  hackathon MVP crawling user-supplied public sites, same accepted-risk posture as the
  SSRF TOCTOU note above.
- Two fixture builders exist (`scripts/build_fixture_session.py`, used by the test suite, and
  `ingest/build_fixture.py`, kept only because a test reuses its deterministic-embedding
  helper). They are not wired into the running server, so this does not affect normal use —
  if you regenerate `data/sessions/fixture_demo` by hand, use
  `python -m scripts.build_fixture_session`.

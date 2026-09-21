# Architecture — Dynamic Website RAG Flow-Aware Chatbot

> **Status:** Planning-stage hackathon MVP — no application code exists yet. This document specifies the target architecture and data flow, derived from [FINAL_PLAN_IMPROVED.md](FINAL_PLAN_IMPROVED.md), the authoritative product plan.

**Core promise:** Enter a website, crawl up to 20 relevant pages, build a temporary knowledge base, and ask questions about that website with answers grounded in its content and accompanied by source links.

The system has two primary pipelines that share a common session boundary:

- **Ingestion pipeline** — URL → validate → crawl → clean → detect services → chunk → embed → session-scoped vector index.
- **Conversation pipeline** — chat message → deterministic flow routing → evidence-gated retrieval → grounded LLM answer → citations.

---

## 1. Goals and Non-Goals

| In scope (MVP) | Out of scope (this phase) |
|---|---|
| User-provided URL, same-domain BFS crawl (max 20 pages) | Arbitrary multi-domain crawling |
| Deterministic, rule-based flow routing | LLM-based intent classification per turn |
| FAISS `IndexFlatIP`, one index per crawl session | Cloud-hosted / distributed vector DB |
| SQLite persistence (sessions, messages, events) | Production data warehouse / analytics stack |
| Streamlit UI | Multi-user auth, embeddable widget |
| Evidence-gated, citation-backed answers | Reranking, hybrid BM25 + vector search |
| Static-HTML scraping (`requests` + `BeautifulSoup`) | Playwright / JS-rendered crawling |

## 2. Tech Stack

| Layer | Choice |
|---|---|
| UI | Streamlit |
| Backend | FastAPI |
| Chat model | Amazon Nova Pro (`amazon.nova-pro-v1:0`) via Bedrock Converse API |
| Embeddings | Amazon Titan Embed Text v2 (`amazon.titan-embed-text-v2:0`) |
| Vector store | FAISS `IndexFlatIP`, session-scoped |
| Relational store | SQLite (sessions, crawl_jobs, messages, flow_events, documents) |
| Scraper | `requests` + `BeautifulSoup`, static HTML only |
| Raw storage | Local filesystem, per-session `raw/` + `cleaned/` |

## 3. Feature Inventory

Every feature maps to a numbered process in the [Data Flow Diagrams](#4-data-flow-diagrams-dfd) below, so the DFD can be read as a diagram of the product's features.

| # | Feature | DFD process |
|---|---|---|
| 1 | Website URL intake, validation and canonicalization | 1.0 |
| 2 | SSRF-safe, robots.txt-aware, bounded BFS crawl (max 20 pages) | 2.0 |
| 3 | HTML cleaning and service discovery | 3.0 |
| 4 | Heading-aware, structure-preserving chunking | 3.0 |
| 5 | Titan embeddings and session-scoped FAISS index build | 4.0 |
| 6 | Deterministic conversation flow routing | 5.0, [4.6](#46-intent--flow-routing--decision-cascade) |
| 7 | Conversation context and follow-up resolution | 5.0 |
| 8 | Evidence-gated top-k retrieval | 6.0 |
| 9 | Grounded, cited Nova Pro answers with prompt-injection defenses | 6.0 |
| 10 | Session-isolated knowledge base (one crawl = one namespace) | D1 / D4 |
| 11 | Persisted sessions, messages, flow events, crawl jobs | D1 / D2 |
| 12 | Flow and retrieval analytics report | 7.0 |
| 13 | Streamlit UI: URL input, crawl status, services, chat, sources | Client |

## 4. Data Flow Diagrams (DFD)

### 4.1 Notation

| Shape | Meaning |
|---|---|
| Rectangle | External entity — outside the system boundary (user, target website, Bedrock) |
| Circle | Process — transforms, routes, or generates data |
| Cylinder | Data store — persisted state (SQLite tables or FAISS/filesystem) |
| Labeled arrow | Data flow — direction and payload of data in motion |

### 4.2 Level 0 — Context Diagram

```mermaid
flowchart LR
    classDef entity fill:#E8F0FE,stroke:#1A73E8,stroke-width:1px,color:#000;
    classDef process fill:#FEF7E0,stroke:#B06000,stroke-width:1px,color:#000;

    USER[User / Judge]:::entity
    SITE[Target Website]:::entity
    BEDROCK[AWS Bedrock<br/>Nova Pro + Titan Embed]:::entity
    SYS((("0.0<br/>Website RAG<br/>Chatbot System"))):::process

    USER -->|Website URL| SYS
    USER -->|Chat message| SYS
    SYS -->|Crawl status, services| USER
    SYS -->|Reply + sources| USER
    SYS -->|Analytics report| USER
    SYS -->|HTTP page requests| SITE
    SITE -->|HTML pages| SYS
    SYS -->|Embed + chat requests| BEDROCK
    BEDROCK -->|Vectors + generated text| SYS
```

### 4.3 Level 1 — System-Wide Data Flow

```mermaid
flowchart TD
    classDef entity fill:#E8F0FE,stroke:#1A73E8,stroke-width:1px,color:#000;
    classDef process fill:#FEF7E0,stroke:#B06000,stroke-width:1px,color:#000;
    classDef store fill:#E6F4EA,stroke:#137333,stroke-width:1px,color:#000;

    USER[User / Judge]:::entity
    SITE[Target Website]:::entity
    BEDROCK[AWS Bedrock]:::entity

    P1(("1.0<br/>Validate +<br/>Canonicalize URL")):::process
    P2(("2.0<br/>Crawl Website<br/>BFS + robots.txt")):::process
    P3(("3.0<br/>Process +<br/>Chunk Content")):::process
    P4(("4.0<br/>Embed +<br/>Build Vector Index")):::process
    P5(("5.0<br/>Route<br/>Conversation")):::process
    P6(("6.0<br/>Retrieve +<br/>Generate Answer")):::process
    P7(("7.0<br/>Log Events +<br/>Analytics")):::process

    D1[(D1 Sessions +<br/>Crawl Jobs)]:::store
    D2[(D2 Messages +<br/>Flow Events)]:::store
    D3[(D3 Raw +<br/>Cleaned Pages)]:::store
    D4[(D4 Vector Index +<br/>Chunk Metadata)]:::store

    USER -->|Website URL| P1
    P1 -->|Canonical URL, allowed host| P2
    P2 -->|HTTP requests| SITE
    SITE -->|HTML pages| P2
    P2 -->|Raw pages| D3
    P2 -->|Crawl status| D1
    D3 -->|Raw pages| P3
    P3 -->|Cleaned pages| D3
    P3 -->|Service catalog, doc metadata| D1
    P3 -->|Chunks + metadata| P4
    P4 -->|Text| BEDROCK
    BEDROCK -->|Embedding vectors| P4
    P4 -->|Index, chunks, manifest| D4

    USER -->|Chat message, session_id| P5
    D1 -->|Session state| P5
    P5 -->|flow_event| D2
    P5 -->|Query + slots| P6
    P6 -->|Query text| BEDROCK
    BEDROCK -->|Query vector| P6
    P6 -->|Similarity search| D4
    D4 -->|Top-k chunks| P6
    P6 -->|Grounded prompt| BEDROCK
    BEDROCK -->|Generated answer| P6
    P6 -->|Message, sources, score, latency| D2
    P6 -->|Reply + sources| USER
    P5 -->|Static reply / fallback| USER

    D1 -->|read| P7
    D2 -->|read| P7
    P7 -->|Analytics report| USER
```

### 4.4 Level 2 — Website Ingestion Pipeline (decomposes 2.0 – 4.0)

```mermaid
flowchart TD
    classDef entity fill:#E8F0FE,stroke:#1A73E8,stroke-width:1px,color:#000;
    classDef process fill:#FEF7E0,stroke:#B06000,stroke-width:1px,color:#000;
    classDef store fill:#E6F4EA,stroke:#137333,stroke-width:1px,color:#000;

    SITE[Target Website]:::entity
    BEDROCK[AWS Bedrock<br/>Titan Embed]:::entity

    P21(("2.1<br/>Check robots.txt")):::process
    P22(("2.2<br/>Fetch Page +<br/>Revalidate Redirects")):::process
    P23(("2.3<br/>Enforce Limits<br/>pages, depth, size")):::process
    P24(("2.4<br/>Extract +<br/>Canonicalize Links")):::process
    P31(("3.1<br/>Clean HTML")):::process
    P32(("3.2<br/>Detect Services")):::process
    P33(("3.3<br/>Structure-aware<br/>Chunking")):::process
    P41(("4.1<br/>Embed Chunks")):::process
    P42(("4.2<br/>Build FAISS Index")):::process

    Q[(BFS Queue)]:::store
    D1[(D1 Sessions +<br/>Crawl Jobs)]:::store
    D3[(D3 Raw +<br/>Cleaned Pages)]:::store
    D4[(D4 Vector Index +<br/>Chunk Metadata)]:::store

    Q -->|next URL| P21
    P21 -->|allowed| P22
    P21 -->|blocked, skip| D1
    P22 -->|request| SITE
    SITE -->|response, redirects| P22
    P22 -->|accepted page| P23
    P23 -->|retained page| D3
    P23 -->|crawl complete| D1
    P23 -->|page| P24
    P24 -->|new canonical URLs| Q

    D3 -->|raw page| P31
    P31 -->|cleaned page| P32
    P32 -->|service labels| D1
    P32 -->|labeled page| P33
    P33 -->|chunks| P41
    P41 -->|text| BEDROCK
    BEDROCK -->|vector| P41
    P41 -->|embedded chunks| P42
    P42 -->|index.faiss, chunks.json, manifest.json| D4
```

### 4.5 Level 2 — Conversational RAG Pipeline (decomposes 5.0 – 6.0)

```mermaid
flowchart TD
    classDef entity fill:#E8F0FE,stroke:#1A73E8,stroke-width:1px,color:#000;
    classDef process fill:#FEF7E0,stroke:#B06000,stroke-width:1px,color:#000;
    classDef store fill:#E6F4EA,stroke:#137333,stroke-width:1px,color:#000;

    USER[User]:::entity
    TITAN[[AWS Bedrock<br/>Titan Embed]]:::entity
    NOVA[[AWS Bedrock<br/>Nova Pro]]:::entity

    P51(("5.1<br/>Classify Intent")):::process
    P52(("5.2<br/>Extract Slots +<br/>Resolve Follow-up")):::process
    P61(("6.1<br/>Embed Query")):::process
    P62(("6.2<br/>FAISS Top-k Search")):::process
    P63(("6.3<br/>Evidence Gate")):::process
    P64(("6.4<br/>Build Grounded Prompt +<br/>Call Nova Pro")):::process
    P65(("6.5<br/>Extract Citations")):::process

    D1[(D1 Session State:<br/>current_service, last_flow)]:::store
    D2[(D2 Messages +<br/>Flow Events)]:::store
    D4[(D4 Vector Index +<br/>Chunk Metadata)]:::store

    USER -->|message| P51
    P51 -->|greeting| USER
    P51 -->|service_list, service_detail,<br/>follow_up, general_qa| P52
    D1 -->|session state| P52
    P52 -->|updated context| D1
    P52 -->|flow_event| D2
    P52 -->|query, service scope| P61
    P61 -->|text| TITAN
    TITAN -->|vector| P61
    P61 -->|vector| P62
    P62 -->|search| D4
    D4 -->|candidate chunks| P62
    P62 -->|ranked chunks| P63
    P63 -->|insufficient evidence| USER
    P63 -->|sufficient evidence| P64
    P64 -->|grounded prompt| NOVA
    NOVA -->|cited answer| P64
    P64 -->|answer| P65
    P65 -->|reply + sources| USER
    P65 -->|message, sources, score| D2
```

### 4.6 Intent / Flow Routing — Decision Cascade

Intent handling is **deterministic and rule-based**, not an LLM classification call. This keeps every turn fast, free, and fully explainable/testable (see the evaluation set in [FINAL_PLAN_IMPROVED.md](FINAL_PLAN_IMPROVED.md#33-evaluation-set)). The router (process 5.1/5.2) evaluates a fixed, ordered cascade of rules against the normalized user message and the session's stored conversation state; the **first matching rule wins** and assigns exactly one flow.

**Order of evaluation** (first match wins):

1. **`greeting`** — message matches a small greeting lexicon (hi, hello, hey, good morning, ...). Answered with a static response; no retrieval, no LLM call.
2. **`service_list`** — message matches "what services / what do you do / what do you offer" style patterns. Retrieves the session's service catalog (built during ingestion) rather than running open-ended retrieval.
3. **`service_detail`** — message names (or fuzzy-matches) one of the session's known services (e.g. "Salesforce"). Sets `current_service` in session state and scopes retrieval with a `service=` metadata filter.
4. **`follow_up`** — message is short/anaphoric ("tell me more", "what about its pricing", "and benefits?") **and** `current_service` or `last_flow` exists in session state. Resolves pronouns/ellipsis using that stored context instead of the raw message.
5. **`general_qa`** — none of the above matched, but the message is a well-formed question. Falls through to unscoped retrieval across the full session knowledge base.
6. **`fallback`** — nothing matched, or matched but the evidence gate (process 6.3, downstream of routing) later finds insufficient similarity. Returns a safe "I couldn't find that" reply instead of a hallucinated answer.

Only flows 3-5 call Titan/FAISS/Nova Pro; `greeting` and `fallback` short-circuit before any retrieval or generation cost is incurred. Every routing decision — matched flow, confidence/rule id, and whether an answer was ultimately produced — is written to `flow_events` for analytics (process 7.0).

```mermaid
flowchart TD
    classDef entity fill:#E8F0FE,stroke:#1A73E8,stroke-width:1px,color:#000;
    classDef decision fill:#FEF7E0,stroke:#B06000,stroke-width:1px,color:#000;
    classDef flow fill:#F3E8FD,stroke:#7C3AED,stroke-width:1px,color:#000;
    classDef store fill:#E6F4EA,stroke:#137333,stroke-width:1px,color:#000;
    classDef terminal fill:#FCE8E6,stroke:#C5221F,stroke-width:1px,color:#000;

    MSG[User message + session_id]:::entity
    NORM["Normalize text<br/>(lowercase, trim, strip punctuation)"]:::decision
    D1[(Session state:<br/>current_service, last_flow, last_sources)]:::store

    Q1{Matches greeting<br/>lexicon?}:::decision
    Q2{Matches service-list<br/>intent pattern?}:::decision
    Q3{Names a known<br/>service?}:::decision
    Q4{Short/anaphoric phrase<br/>AND active context<br/>in session state?}:::decision
    Q5{Well-formed<br/>question?}:::decision

    F_GREET[["flow = greeting<br/>static reply"]]:::flow
    F_LIST[["flow = service_list<br/>retrieve service catalog"]]:::flow
    F_DETAIL[["flow = service_detail<br/>set current_service<br/>retrieve scoped by service"]]:::flow
    F_FOLLOW[["flow = follow_up<br/>resolve pronouns via<br/>session state, reuse scope"]]:::flow
    F_GENQA[["flow = general_qa<br/>retrieve across full<br/>session knowledge base"]]:::flow
    F_FALLBACK[["flow = fallback<br/>safe no-answer reply"]]:::terminal

    RAG["Evidence-gated retrieval +<br/>grounded Nova Pro (6.1-6.5)"]:::decision
    EVID{Evidence gate:<br/>sufficient similarity?}:::decision
    ANSWER["Answer + citations"]:::terminal

    MSG --> NORM --> Q1
    D1 -.->|read context| Q4

    Q1 -->|yes| F_GREET
    Q1 -->|no| Q2
    Q2 -->|yes| F_LIST
    Q2 -->|no| Q3
    Q3 -->|yes| F_DETAIL
    Q3 -->|no| Q4
    Q4 -->|yes| F_FOLLOW
    Q4 -->|no| Q5
    Q5 -->|yes| F_GENQA
    Q5 -->|no| F_FALLBACK

    F_LIST --> RAG
    F_DETAIL --> RAG
    F_FOLLOW --> RAG
    F_GENQA --> RAG
    RAG --> EVID
    EVID -->|yes| ANSWER
    EVID -->|no| F_FALLBACK

    F_DETAIL -.->|write| D1
    F_FOLLOW -.->|write| D1
```

| Flow | Trigger | Session state used | LLM/retrieval? |
|---|---|---|---|
| `greeting` | Greeting lexicon match | none | No — static reply |
| `service_list` | "what services/do you offer" pattern | none | Retrieval only (service catalog) |
| `service_detail` | Named/fuzzy-matched known service | writes `current_service` | Retrieval + Nova Pro, filtered by `service` |
| `follow_up` | Short/anaphoric phrase + existing context | reads + refreshes `current_service`/`last_flow` | Retrieval + Nova Pro, scope inherited from context |
| `general_qa` | Well-formed question, no other match | none | Retrieval (unscoped) + Nova Pro |
| `fallback` | No rule matched, or evidence gate rejects | none | No generation — safe no-answer message |

#### 4.6.1 Compound / Multi-Intent Messages

A single user message can legitimately contain more than one intent, e.g.:

```text
"Hi! What services do you offer, and do you also do Salesforce work?"
   └─ greeting ─┘ └───── service_list ─────┘  └───── service_detail ─────┘
```

The router is single-flow-per-turn by design (MVP), so it resolves this in two steps rather than trying to satisfy every clause at once:

1. **Greeting-prefix stripping.** `greeting` only matches when the *entire* normalized message is a greeting/pleasantry (exact or near-exact lexicon match). Before running the cascade, a leading greeting token/clause ("hi", "hello there", "hey,") is stripped from the message; the remainder continues through the cascade instead of the whole message short-circuiting into a static `greeting` reply. This alone resolves the common "Hi, <real question>" case.
2. **First-match-wins on the remaining substantive intents.** If more than one *non-greeting* rule still matches (e.g. the message both asks for the service list and names a specific service), priority order (`service_list` → `service_detail` → `follow_up` → `general_qa`) decides the single flow for this turn. The unmatched clause is **not** silently answered — it is simply not addressed yet.
3. **Deferred remainder becomes a natural follow-up.** Because `last_flow`/`current_service`/`last_sources` are persisted every turn, the un-answered clause is normally what the user asks next ("...and what about Salesforce?"), which the `follow_up` rule then resolves using the state written by this turn.

This is a **documented MVP limitation, not a bug**: true multi-intent handling (splitting a message into clauses and answering each with its own retrieval + evidence gate, then merging citations) is listed as a stretch goal, not required for the core promise in [FINAL_PLAN_IMPROVED.md §36](FINAL_PLAN_IMPROVED.md#36-stretch-goals). If it's needed for the demo, the lowest-effort upgrade is a pre-router clause splitter (split on "and", "also", "?") that runs the cascade per clause and returns one answer per matched clause with its own citations, still using the same evidence gate per clause.

## 5. Component Architecture

```mermaid
flowchart TD
    classDef client fill:#F3E8FD,stroke:#7C3AED,stroke-width:1px,color:#000;
    classDef backend fill:#FEF7E0,stroke:#B06000,stroke-width:1px,color:#000;
    classDef bedrock fill:#FCE8E6,stroke:#C5221F,stroke-width:1px,color:#000;
    classDef data fill:#E6F4EA,stroke:#137333,stroke-width:1px,color:#000;
    classDef external fill:#E8F0FE,stroke:#1A73E8,stroke-width:1px,color:#000;

    WEB[Target Website]:::external

    subgraph Client
        ST[Streamlit App<br/>URL input, crawl status,<br/>services, chat, sources]:::client
    end

    subgraph Backend["FastAPI Backend"]
        API[REST API<br/>/ingest /chat /sessions /analytics]:::backend
        ING[Ingestion Pipeline]:::backend
        FR[Flow Router]:::backend
        RAG[RAG Service]:::backend
        ANL[Analytics Service]:::backend
    end

    subgraph Bedrock["AWS Bedrock"]
        NOVA[[Nova Pro<br/>chat completion]]:::bedrock
        TITAN[[Titan Embed Text v2<br/>embeddings]]:::bedrock
    end

    subgraph Data["Session-scoped Data Layer"]
        FS[(Raw + Cleaned Pages)]:::data
        VDB[(FAISS IndexFlatIP<br/>+ chunk metadata)]:::data
        SQL[(SQLite<br/>sessions, crawl_jobs,<br/>messages, flow_events, documents)]:::data
    end

    ST -->|REST/JSON| API
    API -->|reply, status, report| ST
    API --> ING
    API --> FR
    FR --> RAG
    FR --> SQL
    ING --> WEB
    WEB --> ING
    ING --> FS
    ING --> TITAN
    ING --> VDB
    ING --> SQL
    RAG --> TITAN
    RAG --> VDB
    RAG --> NOVA
    RAG --> SQL
    ANL --> SQL
    ANL --> ST
```

## 6. Sequence Diagram — Sample Chat Turn

```mermaid
sequenceDiagram
    participant U as User
    participant ST as Streamlit UI
    participant API as FastAPI Backend
    participant FR as Flow Router
    participant RAG as RAG Service
    participant EMB as Titan Embed v2
    participant VDB as Session FAISS Index
    participant LLM as Nova Pro
    participant DB as SQLite

    U->>ST: types "Tell me about Salesforce"
    ST->>API: POST /chat { session_id, message }
    API->>DB: insert user message
    API->>FR: route(session_id, message)
    FR->>DB: load session state
    Note over FR: deterministic rules -> flow=service_detail, slot=Salesforce
    FR->>DB: update current_service, insert flow_event
    FR->>RAG: answer(query, session_id, service=Salesforce)
    RAG->>EMB: embed(query)
    EMB-->>RAG: query vector
    RAG->>VDB: similarity_search(k=8-12, filter=service)
    VDB-->>RAG: top chunks + metadata
    RAG->>RAG: evidence gate check
    alt evidence sufficient
        RAG->>LLM: grounded prompt (chunks as untrusted context)
        LLM-->>RAG: cited answer
    else evidence insufficient
        RAG-->>FR: fallback message
    end
    RAG-->>FR: { reply, sources[] }
    FR->>DB: insert assistant message + sources_json + latency
    FR-->>API: { reply, flow, sources }
    API-->>ST: 200 { reply, flow, sources }
    ST-->>U: renders answer + clickable sources
```

## 7. Session and Knowledge Isolation

> One user website crawl = one isolated knowledge session.

Each session owns its own vector index, chunk metadata, and raw/cleaned pages, so content from one website never leaks into another website's Q&A:

```text
data/sessions/<session_id>/
├── raw/              # saved raw HTML per retained page
├── cleaned/          # cleaned text per retained page
├── index.faiss        # session-scoped FAISS IndexFlatIP
├── chunks.json         # chunk text + metadata, aligned to index vectors
└── manifest.json       # crawl + embedding summary for the session
```

Conceptual session record:

```json
{
  "session_id": "abc123",
  "website_url": "https://example.com",
  "normalized_host": "example.com",
  "status": "ready",
  "pages_crawled": 18,
  "chunks": 143,
  "services": ["Salesforce", "Data Engineering"]
}
```

## 8. Data Model

```text
sessions(
    id, website_url, normalized_host, status,
    created_at, updated_at,
    pages_discovered, pages_retained, chunks, meta
)

crawl_jobs(
    id, session_id, status,
    started_at, finished_at,
    pages_discovered, pages_retained, pages_failed, error
)

documents(
    id, session_id, url, title, service, status, created_at
)

messages(
    id, session_id, role, content, flow,
    sources_json, retrieval_score, latency_ms, created_at
)

flow_events(
    id, session_id, flow, confidence, matched, answered, created_at
)
```

## 9. API Contract

| Endpoint | Purpose | Response highlights |
|---|---|---|
| `POST /ingest` | Phase 1: crawl a URL and build its site map, creates a session | `{ session_id, job_id, status }` |
| `GET /ingest/{job_id}` | Poll crawl/build progress | `{ status, pages_discovered, pages_retained, pages_failed, chunks, services, sitemap: { pages, failures } }` |
| `POST /ingest/{job_id}/build` | Phase 2: turn a ready site map (`status="sitemap_ready"`) into a queryable knowledge base | `{ session_id, job_id, status }` |
| `POST /chat` | Ask a question within a session | `{ reply, flow, sources: [{title, url}] }` |
| `GET /sessions/{session_id}` | Full transcript + crawl status + services | website, crawl status, services, messages, flow events |
| `GET /analytics/report?session_id=` | Flow and retrieval analytics | pages crawled, fallback rate, latency, top sources |

`crawl_jobs.status` progresses `queued -> crawling -> sitemap_ready -> building -> done`
(or `failed` at any stage). Chat is only unlocked once status is `done`.

Example:

```json
POST /chat
{ "session_id": "abc123", "message": "Tell me about Salesforce" }

200 OK
{
  "reply": "...",
  "flow": "service_detail",
  "sources": [
    { "title": "Salesforce Services", "url": "https://example.com/services/salesforce" }
  ]
}
```

## 10. Security Architecture

- **SSRF protection** — allow only `http`/`https`; resolve and validate the hostname; reject localhost/private/internal destinations; restrict crawling to the initial site's allowed host.
- **Redirect revalidation** — every redirect target is re-validated against the same host/network checks as the original URL; the original URL's validity does not carry over to its redirect target.
- **Bounded crawling** — hard cap of 20 retained pages, configurable max depth, request timeout, and response-size limit protect against unexpectedly large or hostile sites.
- **robots.txt compliance** — checked before every fetch.
- **Prompt-injection defense** — retrieved website content is passed to Nova Pro as explicitly untrusted reference data; the system prompt instructs the model never to follow instructions embedded in that content.
- **Evidence gate** — the model is never forced to answer; insufficient similarity triggers a safe fallback instead of a hallucinated response.
- **Secrets handling** — AWS credentials stay server-side (FastAPI backend); never exposed to the Streamlit/browser client.
- **Storage isolation** — vector indexes and SQLite files are kept outside any publicly served static directory.

## 11. Repository Structure

```text
hackathon/
├── architecture.md
├── FINAL_PLAN_IMPROVED.md
├── requirements.txt
├── .env
├── .gitignore
│
├── data/
│   └── sessions/
│       └── <session_id>/
│           ├── raw/
│           ├── cleaned/
│           ├── index.faiss
│           ├── chunks.json
│           └── manifest.json
│
├── ingest/
│   ├── url_validator.py
│   ├── canonicalizer.py
│   ├── robots.py
│   ├── crawler.py
│   ├── extractor.py
│   ├── service_detector.py
│   ├── chunker.py
│   └── pipeline.py
│
├── app/
│   ├── config.py
│   ├── bedrock.py
│   ├── sessions.py
│   ├── vectorstore.py
│   ├── retrieval.py
│   ├── rag.py
│   ├── flows.py
│   ├── analytics.py
│   ├── db.py
│   └── main.py
│
├── ui/
│   └── streamlit_app.py
│
└── tests/
    ├── test_crawler.py
    ├── test_canonicalizer.py
    ├── test_retrieval.py
    ├── test_flows.py
    └── test_questions.json
```

## 12. References

- [FINAL_PLAN_IMPROVED.md](FINAL_PLAN_IMPROVED.md) — full product plan, milestones, verification checklist, and evaluation set.

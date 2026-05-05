# Architecture

This document explains *why* the system looks the way it does — for an interview reviewer who already understands what RAG is and wants to evaluate the design choices.

## Goals & non-goals

**Goals**
- Answer course-material questions with grounded, cited answers.
- Stream tokens for low time-to-first-token UX.
- Be measurable: every retrieval/generation change must show up in the eval scores.
- Be operable: structured logs, request IDs, health probes, migrations, CI gates.

**Non-goals (explicit)**
- Auth, multi-tenancy, RBAC. The schema has a `user_id` slot but auth itself is out of scope for the portfolio cut.
- Multimodal (figure/table) RAG. Future work — see the README.
- Real-time collaboration / WebSocket multiplexing.

## Component view

```
┌─────────────────────────────────────────────────────────────────────┐
│                              React SPA                              │
│                                                                     │
│   ChatWindow ─► useChat ─► fetch-event-source ─► /chat/stream       │
│   DocumentList ─► useDocuments (TanStack Query) ─► /documents       │
└─────────────────────────────────────────────────────────────────────┘
                                  │  HTTPS
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                            FastAPI app                              │
│                                                                     │
│   middleware: request_id, CORS, rate_limit, exception handler       │
│                                                                     │
│   ┌──────────────────┐     ┌──────────────────┐                    │
│   │  /documents      │     │  /chat/stream    │                    │
│   │  POST upload     │     │  POST → SSE      │                    │
│   │   ▼ BG task      │     │   ▼              │                    │
│   │  ingestion svc   │     │  RagPipeline     │                    │
│   └──────────────────┘     └──────────────────┘                    │
│            │                        │                              │
│            ▼                        ▼                              │
│   chunker → embedder      retriever (hybrid + RRF) → reranker      │
│            │              │                                         │
│            └──────► pgvector ◄─────┘   ─►   generator (OpenAI)     │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                        ┌───────────────────────┐
                        │ PostgreSQL + pgvector │
                        │  HNSW + tsvector      │
                        └───────────────────────┘
```

## Key design decisions

### 1. Hybrid retrieval with RRF, not pure dense
Vector search misses queries anchored on specific keywords ("formula for cosine similarity", code identifiers, named theorems). BM25 misses paraphrases. We run both, top-20 each, and fuse with Reciprocal Rank Fusion (k=60). RRF doesn't need score calibration between the two retrievers — it operates purely on ranks. This is the same pattern Anserini and most production RAG stacks converged on.

### 2. Cross-encoder reranking on top of the fused union
The retriever returns ~30 candidates. We pass all of them through a small cross-encoder (`ms-marco-MiniLM-L-6-v2`, ~80 MB) and keep the top-5 for generation. The reranker swap point is clean: it's a `Reranker` Protocol with three implementations (local, Cohere, no-op). The Cohere swap is a one-line config change for higher-recall production use.

### 3. pgvector over a dedicated vector DB
- One database to operate, back up, and reason about.
- HNSW on pgvector hits ≥99% recall@10 at our scale (≤1M chunks) with a few-millisecond p99.
- Hybrid search becomes trivial because tsvector and `vector` columns share the same row — no cross-store consistency to manage.
- **Tradeoff acknowledged:** at 10M+ vectors, Qdrant or Vespa beat pgvector on QPS at high recall. The retriever is encapsulated, so this swap is mechanical when it matters.

### 4. SSE over WebSockets
The chat stream is unidirectional (server → client). SSE is one HTTP response, gets through proxies cleanly, auto-reconnects, and doesn't need a separate stateful protocol. We use `sse-starlette` server-side and `@microsoft/fetch-event-source` client-side (the spec'd `EventSource` doesn't support POST + body).

### 5. Background ingestion via FastAPI BackgroundTasks
For a portfolio project, BackgroundTasks is the simplest thing that actually works. The job runs in the same process; if the API restarts mid-ingestion the document gets stuck in `processing`. **For production**, swap in [Arq](https://arq-docs.helpmanual.io/) — Redis-backed, async-native, durable retries — without changing the ingestion service signature. Documented explicitly so reviewers see we picked the simpler thing on purpose, not by oversight.

### 6. Generated tsvector column with GIN index
The `chunks.content_tsv` column is `GENERATED ALWAYS AS (to_tsvector('english', content)) STORED`. The application never writes to it — Postgres keeps it consistent automatically and the `gin` index makes BM25-style queries sub-millisecond.

### 7. HNSW with cosine ops, not IVFFlat
HNSW gives consistent latency without a training step. IVFFlat is faster to build but requires retraining when the corpus shifts and has worse recall at the same `ef`. For a streaming-write workload like document upload, HNSW's incremental insert wins.

## Request lifecycle: a single `/chat/stream` call

```
1. POST /chat/stream  { question, course_code? }
2. RequestContextMiddleware sets x-request-id (returned in response header).
3. Route handler instantiates an EventSourceResponse over an async generator.
4. Generator opens its own DB session (request-scoped session would close
   before streaming finishes).
5. RagPipeline.stream():
     a. embedder.embed_one(question)                          [~30ms OpenAI]
     b. SQL: dense (HNSW) and sparse (gin) in parallel        [~5ms]
     c. RRF fuse to ~30 candidates                            [<1ms]
     d. cross-encoder.rerank(top_n=5)                         [~80ms CPU]
     e. emit "context" event with chunk metadata
     f. OpenAI chat.completions stream=True
        - emit one "token" event per delta                    [time-to-first-token ~400ms]
     g. parse [n] markers from accumulated text
     h. emit "citations" event with resolved chunks
     i. emit "done" event
6. EventSourceResponse closes; session commits & closes.
7. structlog flushes a single line with request_id, retrieval counts,
   answer length, citation count.
```

## Failure modes and what we do about them

| Failure | Symptom | Mitigation |
| --- | --- | --- |
| OpenAI 5xx | embed/generate raises | `tenacity` retry with exponential backoff (4 attempts, 1–10s) on the embedder; generator surface as SSE `error` event |
| Empty retrieval | `retrieved == []` | Pipeline emits a deterministic "couldn't find anything" message; never fabricates context |
| PDF parse error | `pypdf` exception per page | Skipped page is logged; document marked `failed` only if zero pages parse |
| Reranker model OOM | sentence-transformers crash on import | `RERANKER_PROVIDER=none` falls back to RRF-ordered top-N |
| DB connectivity | `/readyz` returns 500 | Fly's HTTP check restarts the machine; deploys gate on health |
| Hot reload during streaming | open SSE connections drop | Client `useChat` shows "Connection lost"; user re-asks |

## Observability

- **Logs** — structlog. JSON in production (one event per log line, includes `request_id`, `level`, timing fields), pretty in dev.
- **Tracing** — Langfuse hooks live alongside the pipeline; setting `LANGFUSE_*` env vars enables the trace exporter without touching code.
- **Metrics** — OpenTelemetry FastAPI instrumentation auto-emits HTTP latency / status histograms. Wire into a collector via OTEL_EXPORTER_OTLP_ENDPOINT.
- **Eval** — Ragas on the golden dataset gates merges. See [EVAL.md](./EVAL.md).

## Deployment topology (Fly.io)

```
       ┌────────────┐         ┌──────────────────────┐
 user ►│ fly proxy  │────────►│ scholaria (1 machine)│
       └────────────┘         │ uvicorn :8000        │
                              └──────┬───────────────┘
                                     │ private 6PN
                              ┌──────▼───────────────┐
                              │ scholaria-db          │
                              │ Fly Postgres + pgvec  │
                              └───────────────────────┘
```

- Frontend ships as a static bundle (Vite build → nginx) on its own Fly machine or a CDN.
- Auto-stop is enabled — idle traffic costs $0; cold start is ~2s for the API.
- Migrations run via `release_command = "alembic upgrade head"` so a deploy never lands code that depends on a column the DB doesn't have.

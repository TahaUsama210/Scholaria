# Architecture

This document explains why the system looks the way it does. It is written for a reviewer who already knows what RAG is and wants to judge the design choices. Measured numbers come from [EVAL.md](EVAL.md) and the README. Nothing here is aspirational.

## Goals and non-goals

**Goals**
- Answer questions about course material with grounded, cited answers, and refuse when the material doesn't cover the question.
- Stream tokens, so the first words appear quickly.
- Be measurable: a retrieval or prompt change must show up in eval numbers that gate CI.
- Be operable: request ids, metrics with SLO alerts, traces, health probes, migrations in the deploy pipeline.
- Run at zero cost: no paid API is required to develop, test, evaluate or demo.

**Non-goals**
- Auth, multi-tenancy, RBAC.
- Multimodal (figure or table) retrieval.
- A durable job queue. This is a known gap; see [Failure modes](#failure-modes).

## Component view

```
┌──────────────────────────────── React SPA ─────────────────────────────────┐
│ ChatWindow ─► useChat ─► fetch-event-source ─► POST /api/chat/stream (SSE) │
│ LibraryPanel ─► useDocuments (TanStack Query) ─► /api/documents            │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     │ same origin (SPA served by the API image)
┌──────────────────────────── FastAPI (python -m app) ───────────────────────┐
│ RequestContextMiddleware: x-request-id, HTTP metrics by route template     │
│ UploadSizeLimitMiddleware · CORS · slowapi per-IP limits · 500 handler     │
│                                                                            │
│ /api/documents ── register_upload ──► BackgroundTask: ingest_document      │
│                                         pypdf → tiktoken chunker → embed   │
│ /api/chat/stream ─► RagPipeline                                            │
│     condense(history) → embed → dense ∥ sparse → RRF → rerank → gate       │
│                                                          → generate (SSE)  │
│ /healthz · /readyz · /metrics (or private METRICS_PORT)                    │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     │
                     ┌───────────────▼────────────────┐
                     │ PostgreSQL 16 + pgvector       │
                     │ chunks.embedding  HNSW cosine  │
                     │ chunks.content_tsv GIN         │
                     └────────────────────────────────┘
```

Every stage is a small module behind an interface, so providers can be swapped by configuration:

| Stage | Implementations | Selected by |
|---|---|---|
| Embeddings | OpenAI-compatible API, local sentence-transformers, deterministic hashing | `EMBEDDING_PROVIDER` |
| Reranker | local cross-encoder, Cohere, none | `RERANKER_PROVIDER` |
| Generator | any OpenAI-compatible endpoint (OpenAI, Groq, Ollama), deterministic fake | `LLM_PROVIDER`, `LLM_BASE_URL` |

The hashing embedder and the fake generator are not mocks. They are real implementations with deterministic output. That is what lets the integration tests, e2e tests, CI evals and load tests run the production code path with no network access.

## Key design decisions

### 1. Hybrid retrieval, but the reranker is what earns its keep

Dense and sparse candidates (top 20 each) are fused with Reciprocal Rank Fusion (k=60). RRF works purely on ranks, so it needs no score calibration between cosine similarity and `ts_rank_cd`.

The eval tells a more nuanced story than "hybrid beats dense":

- **RRF alone does not measurably beat dense-only** on the demo corpus: +0.020 MRR@10, 95% CI [−0.033, +0.074]. It helps keyword-anchored questions (0.897 vs 0.810) but hurts paraphrases, where sparse matching is weak.
- **The cross-encoder reranker produces the gain:** +0.101 MRR@10 and +0.149 hit@1 over dense, with both intervals excluding zero.

Hybrid stays because it is cheap (about 6 ms of SQL for both queries) and gives the reranker a wider candidate pool, about 25 chunks, that includes exact-identifier matches dense search misses. The reranker then fixes the ordering.

Sparse matching ORs the query lexemes (`plainto_tsquery` with `&` replaced by `|`). With the original AND semantics, full-text search matched nothing for 26 of the 47 in-scope questions. Score ties are broken by `(document_id, chunk_index)` rather than random chunk UUIDs, so results are stable for a given ingest.

### 2. A relevance gate instead of trusting the LLM to refuse

Dense retrieval always returns *something*, so "refuse when retrieval is empty" never fires. Before this gate, refusal accuracy was 0/8. The pipeline now refuses when the best reranked chunk scores below `RERANK_MIN_SCORE`: −9 in cross-encoder logits, tuned from the eval's score-floor table. That refuses 6/8 out-of-scope questions with 0/47 false refusals. The refusal is deterministic and needs no LLM call, which also saves tokens. The floor is in the reranker's own units, so it is a setting rather than a constant and must be re-validated when the model changes.

### 3. pgvector rather than a dedicated vector database

- One database to operate, back up and migrate.
- Dense and sparse indexes live on the same row, so hybrid search needs no cross-store consistency.
- The generated column `content_tsv` (`GENERATED ALWAYS AS (to_tsvector('english', content)) STORED`) keeps full-text search in sync without application code. The ORM maps it as `Computed`, so it is never written.
- **Trade-off:** HNSW filters *after* the index search. A selective `course_code` filter could therefore return far fewer than `DENSE_TOP_K` dense hits, because only about `ef_search` (40) candidates survive to the filter. The retriever enables pgvector 0.8's iterative scans (`hnsw.iterative_scan = strict_order`, transaction-local), which keep scanning until the filtered query has enough rows. At very large scale, partitioning by course or a dedicated engine (Qdrant, Vespa) is the next step, and the retriever is encapsulated so that swap stays local.

### 4. SSE, not WebSockets

The chat stream is one-way. SSE is a single HTTP response, passes through proxies cleanly, and needs no extra protocol. The client uses `@microsoft/fetch-event-source`, because the native `EventSource` can't POST a body. The stream protocol:

```
context    [{marker, chunk_id, document_id, document_title, page_number, snippet, score}, ...]   ranked sources, sent first
token      "..."                                                                     repeated
citations  [...]                                                                     only sources actually cited
done       {answer, outcome: answered|no_context, standalone_question?}
error      {message, request_id}                                                     instead of the above on failure
```

`context` arrives first, so the UI can render the sources panel while tokens stream. Raw exception text never reaches the client: errors are mapped to user-safe messages, and the request id ties the message to the server log line.

### 5. Follow-up condensation

When the request includes `history` (at most 10 turns, of which the UI sends the last 6 completed ones), the generator first rewrites the question into a standalone query. Retrieval runs on that query, not the raw follow-up. If condensation fails, the pipeline falls back to the raw question rather than failing the answer.

### 6. Upload registration that can't race

Uploads are deduplicated by SHA-256 content hash. `register_upload` takes a row lock (`SELECT … FOR UPDATE`) and relies on the unique constraint for concurrent first inserts: the losing insert resolves to the winner's row. Before this fix, the background task could start before the request's transaction committed and leave documents stuck in `pending` forever. An integration test reproduces that interleaving and fails without the fix.

- **Failed documents:** reset and re-ingested when the same file is uploaded again.
- **Stale documents:** a `pending` or `processing` document idle longer than `INGEST_STALE_AFTER_SECONDS` (900 s by default, e.g. after a restart mid-ingest) is treated the same way.

### 7. One production image

The root `Dockerfile` builds the SPA (Node stage), installs CPU-only PyTorch and the backend with `uv`, and bakes both default models and the tiktoken files into the image with `HF_HUB_OFFLINE=1`. It runs as non-root (uid 10001) with `python -m app`, which honours `$PORT`. The result is about 580 MB compressed, against 3.1 GB for the previous image.

- **Trade-off of baking the models in:** about 176 MB more image, in exchange for cold starts that never touch Hugging Face.
- **Single origin:** the API serves the built SPA from `STATIC_DIR`, so there is no second service, no CORS in production, and the request id flows end to end.

## Request lifecycle: one chat question

```
1. POST /api/chat/stream {question, course_code?, history?}
2. RequestContextMiddleware assigns (or accepts) x-request-id and starts the HTTP timer.
3. slowapi checks the per-IP chat limit (RATE_LIMIT_CHAT, default 20/minute) → 429 + retry-after.
4. The handler returns an EventSourceResponse. The generator opens its own DB session,
   because a request-scoped session would close before streaming finishes.
5. RagPipeline.stream():
     a. condense(history)              rag.condense span   (only with history)
     b. embed query                    rag.embed           ~7 ms local MiniLM
     c. dense + sparse SQL             rag.retrieve        ~6 ms
     d. RRF fuse (~25 candidates)
     e. cross-encoder rerank → top 5   rag.rerank          ~970 ms mean on CPU (dominant cost)
     f. relevance gate                 → refusal path if top score < RERANK_MIN_SCORE
     g. emit `context`
     h. stream completion              rag.generate        emits `token` events, records TTFT
     i. parse [n] markers → `citations`, then `done`
6. Metrics: stage histograms, outcome counter, token counts. One structured log line per stage.
```

Measured end to end on CPU with real local models and the fake LLM: about 1.1 s per question once warm, and 5.8 s for the first question, which includes model load.

## Failure modes

| Failure | What happens | Mitigation |
|---|---|---|
| LLM/embedding API 5xx, timeout, 429 | `embed` retries transient errors with backoff; generation failure becomes an SSE `error` event | `scholaria_rag_requests_total{outcome="error"}` feeds the availability SLO alert |
| Misconfiguration (e.g. missing API key) | model warm-up at startup logs a warning; each chat request then fails fast | user-safe `error` event; the API key is only required when that provider is actually used |
| Cold start | torch and two models take seconds to load | loaded in a worker thread during startup, before the app accepts traffic, so the first question never blocks the event loop or `/healthz` |
| Question outside the corpus | top rerank score below the floor | deterministic refusal, `outcome="no_context"` |
| Empty retrieval | no candidates | same refusal path |
| Encrypted, corrupt or image-only PDF | `IngestionError` with a user-safe message | document marked `failed` with that message; re-upload retries |
| Restart mid-ingest, or Fly auto-stopping an idle machine | document left `pending`/`processing` | re-upload after `INGEST_STALE_AFTER_SECONDS`. A running ingest refreshes `updated_at` between embedding batches, so a long ingest is never mistaken for a dead one. A durable queue (Arq) is the proper fix |
| Database down | `/readyz` returns 503 within `READYZ_TIMEOUT_SECONDS` (3 s); connect timeout 5 s | Fly checks `/healthz` (liveness only), so a DB outage doesn't pull every machine from rotation |
| Oversized upload | 413 from middleware: a declared `Content-Length` is checked up front, and chunked bodies are counted while they stream (`MAX_UPLOAD_MB`) | nothing over the limit reaches the multipart parser's disk spool |
| Abuse | per-IP limits on chat and upload, keyed on `CLIENT_IP_HEADER` (`Fly-Client-IP`), which the edge proxy sets | not `X-Forwarded-For`, whose leftmost entry the client controls; in-memory per process, so set `RATE_LIMIT_STORAGE_URI` (Redis) when running more than one process |
| Client disconnects mid-answer | generator closed | not counted as an error (tested) |

## Observability

- **Metrics** (`prometheus-client`):

  | Metric | What it records |
  |---|---|
  | `scholaria_http_requests_total{method,route,status}` | requests, labelled by route *template* to keep cardinality bounded |
  | `scholaria_http_request_duration_seconds` | HTTP latency |
  | `scholaria_rag_stage_duration_seconds{stage}` | condense, embed, retrieve, rerank, generate_ttft, generate_total |
  | `scholaria_rag_requests_total{outcome}` | answered, no_context, error |
  | `scholaria_rag_retrieved_chunks` | chunks retrieved per question |
  | `scholaria_llm_tokens_total{kind}` | LLM token usage |
  | `scholaria_ingest_documents_total{status}` | ingestion outcomes |
  | `scholaria_ingest_duration_seconds` | ingestion time |

- **SLOs** ([`ops/prometheus/rules`](../ops/prometheus/rules)):
  - p95 time to first token ≤ 2.5 s.
  - 99% chat availability, with multi-window, multi-burn-rate alerts.
  - Both are unit-tested with `promtool test rules`.
- **Dashboards:** Grafana is provisioned from [`ops/grafana`](../ops/grafana), with Prometheus and Jaeger datasources.
- **Traces:** OpenTelemetry spans for each RAG stage and `ingest.document`, exported over OTLP/HTTP only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. FastAPI's own request spans are used, and probes are excluded.
- **Logs:** structlog JSON lines with `request_id`. The same id is returned in `x-request-id` and shown in UI error messages.

## Deployment topology (Fly.io)

```
            ┌────────────┐        ┌───────────────────────────────┐
 browser ──►│ fly-proxy  │──────► │ scholaria machine (2 GB)      │
            └────────────┘ :8000  │ python -m app: API + SPA      │
                                  │ :9091 /metrics (private only) │◄── Fly Prometheus
                                  └───────────────┬───────────────┘
                                                  │ private network
                                  ┌───────────────▼───────────────┐
                                  │ Postgres + pgvector           │
                                  └───────────────────────────────┘
```

- `release_command = "alembic upgrade head"` runs in a temporary machine before the rolling deploy replaces anything, so code never lands ahead of its schema.
- Machines auto-stop when idle and auto-start on request.
- Rate limits key on `Fly-Client-IP`, which fly-proxy sets. `FORWARDED_ALLOW_IPS="*"` is only there so the app sees the original scheme.
- The private metrics server binds `::` because Fly's internal network is IPv6.
- The VM is 2 GB because the API measured 788 MiB with both local models loaded.

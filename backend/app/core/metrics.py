"""Prometheus metrics (default registry, exposed at GET /metrics)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import Counter, Histogram

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)

HTTP_REQUESTS = Counter(
    "scholaria_http_requests_total",
    "HTTP requests by method, route template and status code.",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "scholaria_http_request_duration_seconds",
    "HTTP request duration (for SSE: until the stream closes).",
    ["method", "route"],
    buckets=_LATENCY_BUCKETS,
)

RAG_STAGES = ("embed", "retrieve", "rerank", "generate_ttft", "generate_total", "condense")
RAG_STAGE_DURATION = Histogram(
    "scholaria_rag_stage_duration_seconds",
    "Duration of each RAG pipeline stage.",
    ["stage"],
    buckets=_LATENCY_BUCKETS,
)
RAG_OUTCOMES = ("answered", "no_context", "error")
RAG_REQUESTS = Counter(
    "scholaria_rag_requests_total",
    "RAG pipeline runs by outcome.",
    ["outcome"],
)
RAG_RETRIEVED_CHUNKS = Histogram(
    "scholaria_rag_retrieved_chunks",
    "Chunks returned by hybrid retrieval (after RRF fusion, before rerank).",
    buckets=(0, 1, 2, 5, 10, 20, 30, 40, 60, 100),
)
LLM_TOKENS = Counter(
    "scholaria_llm_tokens_total",
    "LLM tokens reported by the provider.",
    ["kind"],
)

INGEST_STATUSES = ("ready", "failed")
INGEST_DOCUMENTS = Counter(
    "scholaria_ingest_documents_total",
    "Ingested documents by terminal status.",
    ["status"],
)
INGEST_DURATION = Histogram(
    "scholaria_ingest_duration_seconds",
    "End-to-end document ingestion duration.",
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)

# Pre-create label sets so dashboards show zeros instead of "no data".
for _stage in RAG_STAGES:
    RAG_STAGE_DURATION.labels(stage=_stage)
for _outcome in RAG_OUTCOMES:
    RAG_REQUESTS.labels(outcome=_outcome)
for _kind in ("prompt", "completion"):
    LLM_TOKENS.labels(kind=_kind)
for _status in INGEST_STATUSES:
    INGEST_DOCUMENTS.labels(status=_status)


@contextmanager
def observe_stage(stage: str) -> Iterator[None]:
    """Record the wall time of a RAG stage, including when it raises."""
    start = time.perf_counter()
    try:
        yield
    finally:
        RAG_STAGE_DURATION.labels(stage=stage).observe(time.perf_counter() - start)

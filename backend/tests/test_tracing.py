import time
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core import tracing
from app.core.config import Settings
from tests.helpers import chunk
from tests.test_pipeline import Turn, _make, _run

_exporter = InMemorySpanExporter()


@pytest.fixture
def spans() -> Iterator[tuple[TracerProvider, InMemorySpanExporter]]:
    # The global provider can only be set once per process; later calls reuse it.
    provider = tracing.configure_tracing(Settings(), exporter=_exporter)
    assert provider is not None
    _exporter.clear()
    yield provider, _exporter
    _exporter.clear()


def test_tracing_is_disabled_without_an_endpoint() -> None:
    assert tracing.configure_tracing(Settings(otel_exporter_otlp_endpoint=None)) is None


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://jaeger:4318", "http://jaeger:4318/v1/traces"),
        ("http://jaeger:4318/", "http://jaeger:4318/v1/traces"),
        ("http://jaeger:4318/v1/traces", "http://jaeger:4318/v1/traces"),
    ],
)
def test_traces_url(endpoint: str, expected: str) -> None:
    assert tracing.traces_url(endpoint) == expected


class _Exploding(SpanExporter):
    def export(self, spans: Any) -> SpanExportResult:
        raise ConnectionError("collector down")


def test_quiet_exporter_rate_limits_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[dict[str, Any]] = []

    class _Log:
        def warning(self, event: str, **kw: Any) -> None:
            warnings.append({"event": event, **kw})

    monkeypatch.setattr(tracing, "log", _Log())
    quiet = tracing.QuietExporter(_Exploding(), warn_every_seconds=300)
    assert quiet.export([]) is SpanExportResult.FAILURE
    assert quiet.export([]) is SpanExportResult.FAILURE
    assert len(warnings) == 1 and warnings[0]["event"] == "otel_export_failed"
    assert quiet.force_flush() is True
    quiet.shutdown()


def test_unreachable_collector_does_not_raise() -> None:
    quiet = tracing.QuietExporter(
        OTLPSpanExporter(endpoint="http://127.0.0.1:9/v1/traces", timeout=1)
    )
    start = time.monotonic()
    assert quiet.export([]) is SpanExportResult.FAILURE
    assert time.monotonic() - start < 10
    quiet.shutdown()


async def test_pipeline_emits_stage_spans(
    spans: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = spans
    pipeline, _ = _make([chunk("Overfitting is memorising noise.")], [])
    await _run(pipeline, question="Why?", history=[Turn("user", "What is overfitting?")])
    provider.force_flush()
    names = {s.name for s in exporter.get_finished_spans()}
    assert {"rag.condense", "rag.embed", "rag.retrieve", "rag.rerank", "rag.generate"} <= names
    retrieve = next(s for s in exporter.get_finished_spans() if s.name == "rag.retrieve")
    assert retrieve.attributes is not None and retrieve.attributes["rag.fused_hits"] == 1
    assert retrieve.resource.attributes["service.name"] == "scholaria-api"


async def test_http_requests_are_traced_but_probes_are_not(
    spans: tuple[TracerProvider, InMemorySpanExporter], client: AsyncClient
) -> None:
    provider, exporter = spans
    await client.get("/healthz")
    await client.get("/api/documents/not-a-uuid")
    provider.force_flush()
    names = [s.name for s in exporter.get_finished_spans()]
    assert any("/api/documents/{document_id}" in n for n in names), names
    assert not any("healthz" in n for n in names), names

"""OpenTelemetry tracing.

Spans are always created through the global tracer; they are no-ops unless
``configure_tracing`` installed an SDK provider, which only happens when
OTEL_EXPORTER_OTLP_ENDPOINT is set. An unreachable collector must never
crash the app or flood the logs, so export failures are rate-limited into a
single warning.
"""

from __future__ import annotations

import importlib.util
import logging
import time
from collections.abc import MutableMapping, Sequence
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

from app import __version__
from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)
tracer = trace.get_tracer("scholaria")

_UNTRACED_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})
# FastAPI >= 0.16x traces HTTP requests natively once an SDK provider is installed;
# older versions need opentelemetry-instrumentation-fastapi. Never both (double spans).
NATIVE_FASTAPI_TELEMETRY = importlib.util.find_spec("fastapi.telemetry") is not None
_provider: TracerProvider | None = None


def _untraced(scope: MutableMapping[str, Any]) -> bool:
    return scope.get("path") in _UNTRACED_PATHS


def fastapi_telemetry_kwargs() -> dict[str, Any]:
    """FastAPI(...) kwargs scoping native telemetry to request spans, probes excluded."""
    if not NATIVE_FASTAPI_TELEMETRY:
        return {}
    return {
        "telemetry": {
            "exclude": _untraced,
            "metrics": False,  # Prometheus owns metrics
            "logs": False,  # structlog owns logs
            "operation_spans": False,
        }
    }


class QuietExporter(SpanExporter):
    """Wraps an exporter so failures produce at most one warning per interval."""

    def __init__(self, inner: SpanExporter, *, warn_every_seconds: float = 300.0) -> None:
        self._inner = inner
        self._warn_every = warn_every_seconds
        self._last_warning = float("-inf")

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception as exc:
            self._warn(repr(exc))
            return SpanExportResult.FAILURE
        if result is not SpanExportResult.SUCCESS:
            self._warn("collector rejected or unreachable")
        return result

    def _warn(self, reason: str) -> None:
        now = time.monotonic()
        if now - self._last_warning >= self._warn_every:
            self._last_warning = now
            log.warning("otel_export_failed", reason=reason)

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        # Some exporters return None ("nothing buffered"); only False means failure.
        return self._inner.force_flush(timeout_millis) is not False


def traces_url(endpoint: str) -> str:
    """OTEL_EXPORTER_OTLP_ENDPOINT is a base URL; the traces signal lives at /v1/traces."""
    endpoint = endpoint.rstrip("/")
    return endpoint if endpoint.endswith("/v1/traces") else f"{endpoint}/v1/traces"


def configure_tracing(
    settings: Settings,
    app: FastAPI | None = None,
    *,
    exporter: SpanExporter | None = None,
) -> TracerProvider | None:
    """Install the SDK tracer provider (idempotent). Returns None when disabled."""
    global _provider
    endpoint = settings.otel_exporter_otlp_endpoint
    if exporter is None and not endpoint:
        return None

    if _provider is None:
        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            # The exporter logs every failed batch itself; QuietExporter takes over.
            logging.getLogger("opentelemetry.exporter.otlp").setLevel(logging.CRITICAL)
            otlp = OTLPSpanExporter(endpoint=traces_url(endpoint or ""), timeout=5)
            exporter = QuietExporter(otlp)
        resource = Resource.create(
            {
                "service.name": settings.otel_service_name,
                "service.version": __version__,
                "deployment.environment": settings.environment,
            }
        )
        _provider = TracerProvider(resource=resource)
        _provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(_provider)
        log.info("tracing_enabled", endpoint=endpoint)

    if app is not None and not NATIVE_FASTAPI_TELEMETRY:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls=",".join(p.lstrip("/") for p in _UNTRACED_PATHS),
            tracer_provider=_provider,
        )
    return _provider


def shutdown_tracing() -> None:
    if _provider is not None:
        _provider.shutdown()

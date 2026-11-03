"""Probes and metrics. Mounted at the root (not under /api) and never rate-limited."""

import asyncio

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db import session as db_session

log = get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe — does not touch dependencies."""
    return {"status": "ok"}


@router.get(
    "/readyz",
    responses={503: {"description": "A dependency (the database) is unavailable."}},
)
async def readyz() -> JSONResponse:
    """Readiness probe — verifies DB connectivity; 503 when it is down."""
    try:
        async with asyncio.timeout(get_settings().readyz_timeout_seconds):
            async with db_session.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception as exc:
        log.warning("readiness_check_failed", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=503, content={"status": "unavailable", "checks": {"database": "down"}}
        )
    return JSONResponse({"status": "ready", "checks": {"database": "ok"}})


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Prometheus exposition of the default registry.

    Hidden (404) when METRICS_PORT moves metrics to a private port.
    """
    if request.app.state.settings.metrics_port is not None:
        return Response(status_code=404)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

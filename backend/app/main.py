import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app import __version__
from app.api.router import api_router, probes_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware, UploadSizeLimitMiddleware
from app.core.rate_limit import limiter
from app.core.spa import SPAStaticFiles
from app.core.tracing import configure_tracing, fastapi_telemetry_kwargs, shutdown_tracing
from app.db.session import engine
from app.rag.pipeline import get_pipeline

log = get_logger(__name__)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "-"))


async def _rate_limit_handler(request: Request, exc: Exception) -> JSONResponse:
    detail, retry_after = "rate limit exceeded", 60
    if isinstance(exc, RateLimitExceeded):
        detail = exc.detail
        if exc.limit is not None:  # upper bound: the full window length
            retry_after = exc.limit.limit.get_expiry()
    rid = _request_id(request)
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {detail}", "request_id": rid},
        headers={"retry-after": str(retry_after), "x-request-id": rid},
    )


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    rid = _request_id(request)
    log.error("unhandled_exception", path=request.url.path, request_id=rid, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error.", "request_id": rid},
        headers={"x-request-id": rid},
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    log.info(
        "startup",
        version=__version__,
        environment=settings.environment,
        llm_provider=settings.llm_provider,
        embedding_provider=settings.embedding_provider,
        reranker_provider=settings.reranker_provider,
    )
    if settings.warm_models_on_startup:
        # Loading torch + two models takes seconds; doing it lazily on the first chat
        # would block the event loop (and /healthz) mid-request on every cold start.
        try:
            await asyncio.to_thread(get_pipeline)
            log.info("models_warmed")
        except Exception:  # misconfiguration surfaces per request as an SSE error
            log.warning("model_warmup_failed", exc_info=True)
    yield
    await engine.dispose()
    shutdown_tracing()
    log.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Scholaria API",
        version=__version__,
        description="RAG-powered course Q&A.",
        lifespan=_lifespan,
        **fastapi_telemetry_kwargs(),
    )
    app.state.settings = settings
    app.state.limiter = limiter

    app.add_middleware(
        UploadSizeLimitMiddleware, path="/api/documents", max_bytes=settings.max_upload_bytes
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )
    app.add_middleware(RequestContextMiddleware)  # outermost: sees every response

    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_exception_handler(Exception, _unhandled_handler)

    app.include_router(probes_router)
    app.include_router(api_router)
    configure_tracing(settings, app)

    if settings.static_dir:
        static_dir = Path(settings.static_dir)
        if not (static_dir / "index.html").is_file():
            raise RuntimeError(f"STATIC_DIR={static_dir} has no index.html; build the frontend.")
        # Mounted last so every API route wins over the catch-all.
        app.mount("/", SPAStaticFiles(directory=static_dir, html=True), name="spa")
    return app


app = create_app()

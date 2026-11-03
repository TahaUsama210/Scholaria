"""Chat endpoint — streaming SSE.

Note on cleanup: SSE responses live for the duration of the generator. We
acquire a fresh DB session inside the generator (rather than via FastAPI's
dependency) because the request-scoped session would be closed by the
response finalizer before the generator finishes streaming.
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.core.logging import get_logger, request_id_var
from app.core.metrics import RAG_REQUESTS
from app.core.rate_limit import chat_limit, limiter
from app.db.session import async_session_factory
from app.rag.errors import user_safe_message
from app.rag.pipeline import get_pipeline
from app.schemas.chat import ChatErrorEvent, ChatRequest

log = get_logger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post(
    "/stream",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "SSE stream: context, token…, citations, done (or error).",
            "content": {"text/event-stream": {}},
        },
        429: {"description": "Rate limit exceeded."},
    },
)
@limiter.limit(chat_limit)
async def chat_stream(request: Request, payload: ChatRequest) -> EventSourceResponse:
    request_id = request_id_var.get()

    def error_event(exc: BaseException) -> dict[str, str]:
        log.error("chat_stream_failed", exc_info=exc, error_type=type(exc).__name__)
        body = ChatErrorEvent(message=user_safe_message(exc), request_id=request_id)
        return {"event": "error", "data": body.model_dump_json()}

    async def event_source() -> AsyncIterator[dict[str, str]]:
        try:
            pipeline = get_pipeline()
        except Exception as exc:  # misconfiguration, e.g. a missing API key
            RAG_REQUESTS.labels(outcome="error").inc()
            yield error_event(exc)
            return
        try:
            async with async_session_factory() as session:
                async for ev in pipeline.stream(
                    session,
                    question=payload.question,
                    course_code=payload.course_code,
                    history=payload.history,
                ):
                    yield {"event": ev.type, "data": json.dumps(ev.data, default=str)}
        except Exception as exc:  # the pipeline already counted the error outcome
            yield error_event(exc)

    return EventSourceResponse(event_source(), ping=15)

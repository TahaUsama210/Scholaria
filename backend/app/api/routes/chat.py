"""Chat endpoint — streaming SSE.

Note on cleanup: SSE responses live for the duration of the generator. We
acquire a fresh DB session inside the generator (rather than via FastAPI's
dependency) because the request-scoped session would be closed by the
response finalizer before the generator finishes streaming.
"""
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.rag.pipeline import get_pipeline
from app.schemas.chat import ChatRequest

log = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/stream")
async def chat_stream(payload: ChatRequest) -> EventSourceResponse:
    pipeline = get_pipeline()

    async def event_source() -> AsyncIterator[dict[str, str]]:
        async with async_session_factory() as session:
            try:
                async for ev in pipeline.stream(
                    session, question=payload.question, course_code=payload.course_code
                ):
                    yield {"event": ev.type, "data": json.dumps(ev.data, default=str)}
            except Exception as exc:
                log.exception("chat_stream_failed")
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}

    return EventSourceResponse(event_source(), ping=15)

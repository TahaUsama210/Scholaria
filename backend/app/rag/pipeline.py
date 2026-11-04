"""End-to-end RAG pipeline composition.

The pipeline is intentionally a small, sync-feeling wrapper that yields
SSE-shaped events — keeping the route handler trivial and giving eval a
straightforward seam to record traces against.

Event order: context → token… → citations → done. ``context`` lists the
reranked chunks up-front so the UI can render the citation rail while
tokens stream in.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from functools import lru_cache

from opentelemetry.trace import Status, StatusCode
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.metrics import RAG_REQUESTS, RAG_STAGE_DURATION, observe_stage
from app.core.tracing import tracer
from app.rag.embeddings import Embedder, get_embedder
from app.rag.generator import (
    Generator,
    HistoryTurn,
    StreamEvent,
    citation_payload,
    make_generator,
)
from app.rag.reranker import Reranker, make_reranker
from app.rag.retriever import HybridRetriever, RetrievedChunk

log = get_logger(__name__)

NO_CONTEXT_MESSAGE = (
    "I couldn't find anything in the indexed materials that answers this. "
    "Try rephrasing or upload the relevant document."
)


@dataclass(frozen=True, slots=True)
class RagAnswer:
    answer: str
    chunks: list[RetrievedChunk]
    citations: list[dict[str, object]]


class RagPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        embedder: Embedder,
        reranker: Reranker,
        generator: Generator,
    ) -> None:
        self._settings = settings
        self._retriever = HybridRetriever(embedder=embedder, settings=settings)
        self._reranker = reranker
        self._generator = generator

    @property
    def retriever(self) -> HybridRetriever:
        return self._retriever

    async def stream(
        self,
        session: AsyncSession,
        *,
        question: str,
        course_code: str | None = None,
        history: Sequence[HistoryTurn] | None = None,
    ) -> AsyncGenerator[StreamEvent]:
        try:
            async with aclosing(
                self._stream(session, question=question, course_code=course_code, history=history)
            ) as events:
                async for ev in events:
                    yield ev
        except Exception:
            RAG_REQUESTS.labels(outcome="error").inc()
            raise

    async def condense(self, question: str, history: Sequence[HistoryTurn] | None) -> str:
        """Standalone form of a follow-up question; a no-op without history."""
        if not history:
            return question
        with tracer.start_as_current_span("rag.condense") as span, observe_stage("condense"):
            try:
                standalone = await self._generator.condense(question=question, history=history)
            except Exception:
                # Degrade to the raw question rather than failing the whole answer.
                log.warning("condense_failed", exc_info=True)
                span.set_status(Status(StatusCode.ERROR, "condense failed"))
                return question
        log.info("condensed", question_chars=len(question), standalone_chars=len(standalone))
        return standalone

    @staticmethod
    def _no_context(extra: dict[str, object]) -> list[StreamEvent]:
        RAG_REQUESTS.labels(outcome="no_context").inc()
        return [
            StreamEvent(type="context", data=[]),
            StreamEvent(type="token", data=NO_CONTEXT_MESSAGE),
            StreamEvent(type="citations", data=[]),
            StreamEvent(
                type="done", data={"answer": NO_CONTEXT_MESSAGE, "outcome": "no_context", **extra}
            ),
        ]

    async def _stream(
        self,
        session: AsyncSession,
        *,
        question: str,
        course_code: str | None,
        history: Sequence[HistoryTurn] | None,
    ) -> AsyncGenerator[StreamEvent]:
        question = question.strip()
        standalone = await self.condense(question, history)
        extra: dict[str, object] = {}
        if standalone != question:
            extra["standalone_question"] = standalone

        retrieved = await self._retriever.retrieve(
            session, query=standalone, course_code=course_code
        )
        if not retrieved:
            for ev in self._no_context(extra):
                yield ev
            return

        with tracer.start_as_current_span("rag.rerank") as span, observe_stage("rerank"):
            top = await self._reranker.rerank(
                standalone, retrieved, top_n=self._settings.rerank_top_n
            )
            span.set_attribute("rag.rerank_input", len(retrieved))
            span.set_attribute("rag.rerank_output", len(top))

        min_score = self._settings.effective_rerank_min_score
        if not top or (min_score is not None and top[0].score < min_score):
            # Dense search always returns *something*; a weak best match means the
            # corpus doesn't cover the question, so refuse instead of guessing.
            log.info(
                "below_relevance_threshold",
                top_score=top[0].score if top else None,
                min_score=min_score,
            )
            for ev in self._no_context(extra):
                yield ev
            return

        yield StreamEvent(
            type="context", data=[citation_payload(i, c) for i, c in enumerate(top, start=1)]
        )

        # This span crosses `yield`s, so it is ended explicitly rather than made current
        # (a consumer may close the stream from another context).
        span = tracer.start_span("rag.generate")
        start = time.perf_counter()
        first_token = True
        try:
            async with aclosing(self._generator.stream(question=standalone, chunks=top)) as events:
                async for ev in events:
                    if ev.type == "token" and first_token:
                        first_token = False
                        RAG_STAGE_DURATION.labels(stage="generate_ttft").observe(
                            time.perf_counter() - start
                        )
                    if ev.type == "done" and isinstance(ev.data, dict):
                        ev = StreamEvent(
                            type="done", data={**ev.data, "outcome": "answered", **extra}
                        )
                    yield ev
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
        finally:
            RAG_STAGE_DURATION.labels(stage="generate_total").observe(time.perf_counter() - start)
            span.end()
        RAG_REQUESTS.labels(outcome="answered").inc()


# --- factory --------------------------------------------------------------


@lru_cache(maxsize=1)
def get_pipeline() -> RagPipeline:
    """Process-wide singleton — embedders and cross-encoders are heavy to load."""
    settings = get_settings()
    return RagPipeline(
        settings=settings,
        embedder=get_embedder(),
        reranker=make_reranker(settings),
        generator=make_generator(settings),
    )

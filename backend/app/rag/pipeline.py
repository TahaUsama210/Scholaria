"""End-to-end RAG pipeline composition.

The pipeline is intentionally a small, sync-feeling wrapper that yields
SSE-shaped events — keeping the route handler trivial and giving eval a
straightforward seam to record traces against.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.rag.embeddings import Embedder, make_embedder
from app.rag.generator import Generator, StreamEvent
from app.rag.reranker import Reranker, make_reranker
from app.rag.retriever import HybridRetriever, RetrievedChunk

log = get_logger(__name__)


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

    async def stream(
        self,
        session: AsyncSession,
        *,
        question: str,
        course_code: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        retrieved = await self._retriever.retrieve(
            session, query=question, course_code=course_code
        )
        if not retrieved:
            yield StreamEvent(
                type="token",
                data="I couldn't find anything in the indexed materials that "
                "answers this. Try rephrasing or upload the relevant document.",
            )
            yield StreamEvent(type="citations", data=[])
            yield StreamEvent(type="done", data={"answer": ""})
            return

        top = await self._reranker.rerank(
            question, retrieved, top_n=self._settings.rerank_top_n
        )

        # Emit the chunks up-front so the UI can render the citation rail
        # while tokens stream in.
        yield StreamEvent(
            type="context",
            data=[
                {
                    "marker": i + 1,
                    "chunk_id": str(c.chunk_id),
                    "document_id": str(c.document_id),
                    "document_title": c.document_title,
                    "page_number": c.page_number,
                    "snippet": c.content[:280],
                    "score": c.score,
                }
                for i, c in enumerate(top)
            ],
        )

        async for ev in self._generator.stream(question=question, chunks=top):
            yield ev


# --- factory --------------------------------------------------------------

_pipeline: RagPipeline | None = None


def get_pipeline() -> RagPipeline:
    """Module-level singleton — embedders + cross-encoders are heavy to load."""
    global _pipeline
    if _pipeline is None:
        settings = get_settings()
        if settings.openai_api_key is None:
            raise RuntimeError("OPENAI_API_KEY is required to start the RAG pipeline.")
        _pipeline = RagPipeline(
            settings=settings,
            embedder=make_embedder(settings),
            reranker=make_reranker(settings),
            generator=Generator(
                settings=settings,
                api_key=settings.openai_api_key.get_secret_value(),
            ),
        )
    return _pipeline

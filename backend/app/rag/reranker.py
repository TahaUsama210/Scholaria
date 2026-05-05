"""Cross-encoder reranker.

A cross-encoder scores (query, passage) jointly, which is far more accurate
than the bi-encoder embeddings used during retrieval — at the cost of latency.
Run it on the top-K of the fused list, not the full corpus.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from app.core.config import Settings
from app.core.logging import get_logger
from app.rag.retriever import RetrievedChunk

log = get_logger(__name__)


class Reranker(ABC):
    @abstractmethod
    async def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_n: int
    ) -> list[RetrievedChunk]: ...


class NoopReranker(Reranker):
    async def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_n: int
    ) -> list[RetrievedChunk]:
        return chunks[:top_n]


class _CrossEncoderProto(Protocol):
    def predict(self, sentence_pairs: list[tuple[str, str]]) -> list[float]: ...


class LocalCrossEncoderReranker(Reranker):
    """Default reranker: a small distilled cross-encoder, runs on CPU.

    `cross-encoder/ms-marco-MiniLM-L-6-v2` is ~80MB and scores ~150 pairs/s on
    a modern laptop CPU — comfortably under the latency budget for top-40 input.
    """

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import CrossEncoder  # noqa: PLC0415

        self._model: _CrossEncoderProto = CrossEncoder(model_name)

    async def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_n: int
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []
        pairs = [(query, c.content) for c in chunks]
        scores = self._model.predict(pairs)
        ranked = sorted(zip(chunks, scores, strict=True), key=lambda x: x[1], reverse=True)
        out = [
            RetrievedChunk(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                document_title=c.document_title,
                page_number=c.page_number,
                content=c.content,
                score=float(s),
            )
            for c, s in ranked[:top_n]
        ]
        log.debug("rerank", input=len(chunks), output=len(out))
        return out


class CohereReranker(Reranker):
    """Cohere Rerank — managed API alternative. Set COHERE_API_KEY to enable."""

    def __init__(self, *, api_key: str, model: str = "rerank-english-v3.0") -> None:
        import cohere  # noqa: PLC0415

        self._client = cohere.AsyncClient(api_key=api_key)
        self._model = model

    async def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_n: int
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []
        resp = await self._client.rerank(
            model=self._model,
            query=query,
            documents=[c.content for c in chunks],
            top_n=min(top_n, len(chunks)),
        )
        return [
            RetrievedChunk(
                chunk_id=chunks[r.index].chunk_id,
                document_id=chunks[r.index].document_id,
                document_title=chunks[r.index].document_title,
                page_number=chunks[r.index].page_number,
                content=chunks[r.index].content,
                score=float(r.relevance_score),
            )
            for r in resp.results
        ]


def make_reranker(settings: Settings) -> Reranker:
    match settings.reranker_provider:
        case "local":
            return LocalCrossEncoderReranker(settings.reranker_model)
        case "cohere":
            if settings.cohere_api_key is None:
                raise RuntimeError("RERANKER_PROVIDER=cohere but COHERE_API_KEY is unset.")
            return CohereReranker(api_key=settings.cohere_api_key.get_secret_value())
        case "none":
            return NoopReranker()

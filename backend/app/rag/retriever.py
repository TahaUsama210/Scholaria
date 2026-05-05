"""Hybrid retriever: dense (pgvector) + sparse (Postgres tsvector) fused with RRF.

Reciprocal Rank Fusion (Cormack et al. 2009) is the dead-simple, hard-to-beat
baseline for combining ranked lists. score(d) = sum over lists of 1 / (k + rank).
We use k=60 — Anserini's default and a reasonable choice for top-20 lists.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.rag.embeddings import Embedder

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: UUID
    document_id: UUID
    document_title: str
    page_number: int | None
    content: str
    score: float


class HybridRetriever:
    def __init__(self, *, embedder: Embedder, settings: Settings) -> None:
        self._embedder = embedder
        self._settings = settings

    async def retrieve(
        self,
        session: AsyncSession,
        query: str,
        *,
        course_code: str | None = None,
    ) -> list[RetrievedChunk]:
        query_vec = await self._embedder.embed_one(query)

        dense, sparse = await self._dense_and_sparse(
            session, query=query, query_vec=query_vec, course_code=course_code
        )
        fused = self._rrf(dense, sparse, k=self._settings.rrf_k)

        log.info(
            "retrieval",
            query_chars=len(query),
            dense=len(dense),
            sparse=len(sparse),
            fused=len(fused),
        )
        return fused

    async def _dense_and_sparse(
        self,
        session: AsyncSession,
        *,
        query: str,
        query_vec: list[float],
        course_code: str | None,
    ) -> tuple[list[RetrievedChunk], list[RetrievedChunk]]:
        course_filter = "AND d.course_code = :course_code" if course_code else ""

        dense_sql = text(
            f"""
            SELECT c.id, c.document_id, d.title, c.page_number, c.content,
                   1 - (c.embedding <=> :qvec) AS score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE d.status = 'ready' {course_filter}
            ORDER BY c.embedding <=> :qvec
            LIMIT :k
            """
        ).bindparams(bindparam("qvec", type_=_VectorBind(self._embedder.dim)))

        sparse_sql = text(
            f"""
            SELECT c.id, c.document_id, d.title, c.page_number, c.content,
                   ts_rank_cd(c.content_tsv, plainto_tsquery('english', :q)) AS score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.content_tsv @@ plainto_tsquery('english', :q)
              AND d.status = 'ready' {course_filter}
            ORDER BY score DESC
            LIMIT :k
            """
        )

        params: dict[str, object] = {
            "qvec": query_vec,
            "q": query,
            "k": self._settings.dense_top_k,
        }
        if course_code:
            params["course_code"] = course_code

        dense_rows = (await session.execute(dense_sql, params)).all()
        params["k"] = self._settings.sparse_top_k
        sparse_rows = (await session.execute(sparse_sql, params)).all()

        return (
            [self._row(r) for r in dense_rows],
            [self._row(r) for r in sparse_rows],
        )

    @staticmethod
    def _row(row: object) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=row.id,           # type: ignore[attr-defined]
            document_id=row.document_id,  # type: ignore[attr-defined]
            document_title=row.title,  # type: ignore[attr-defined]
            page_number=row.page_number,  # type: ignore[attr-defined]
            content=row.content,       # type: ignore[attr-defined]
            score=float(row.score),    # type: ignore[attr-defined]
        )

    @staticmethod
    def _rrf(
        dense: list[RetrievedChunk],
        sparse: list[RetrievedChunk],
        *,
        k: int,
    ) -> list[RetrievedChunk]:
        scores: dict[UUID, float] = {}
        keep: dict[UUID, RetrievedChunk] = {}
        for ranked in (dense, sparse):
            for rank, chunk in enumerate(ranked, start=1):
                scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank)
                keep.setdefault(chunk.chunk_id, chunk)

        ordered = sorted(keep.values(), key=lambda c: scores[c.chunk_id], reverse=True)
        # Re-attach the fused score so downstream stages can reason about it.
        return [
            RetrievedChunk(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                document_title=c.document_title,
                page_number=c.page_number,
                content=c.content,
                score=scores[c.chunk_id],
            )
            for c in ordered
        ]


# --- helpers --------------------------------------------------------------

class _VectorBind:
    """Binds list[float] -> pgvector literal for raw text() queries."""

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def bind_processor(self, _dialect: object) -> object:
        def process(value: list[float] | None) -> str | None:
            if value is None:
                return None
            return "[" + ",".join(f"{v:.7f}" for v in value) + "]"
        return process

    @property
    def python_type(self) -> type:
        return list

    def get_dbapi_type(self, _dbapi: object) -> None:
        return None

"""Hybrid retriever: dense (pgvector) + sparse (Postgres tsvector) fused with RRF.

Reciprocal Rank Fusion (Cormack et al. 2009) is the dead-simple, hard-to-beat
baseline for combining ranked lists. score(d) = sum over lists of 1 / (k + rank).
We use k=60 — Anserini's default and a reasonable choice for top-20 lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.metrics import RAG_RETRIEVED_CHUNKS, observe_stage
from app.core.tracing import tracer
from app.rag.embeddings import Embedder

log = get_logger(__name__)

# `:course_code` is NULL for "all courses"; static SQL, no string interpolation.
_COURSE_FILTER = "(CAST(:course_code AS text) IS NULL OR d.course_code = :course_code)"

_DENSE_SQL = f"""
    SELECT c.id, c.document_id, d.title, c.page_number, c.content,
           1 - (c.embedding <=> :qvec) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE d.status = 'ready' AND c.embedding IS NOT NULL AND {_COURSE_FILTER}
    ORDER BY c.embedding <=> :qvec, c.document_id, c.chunk_index
    LIMIT :k
"""  # noqa: S608 - only module constants are interpolated

# plainto_tsquery ANDs every term, which finds nothing for most natural-language
# questions; OR the lexemes instead and let ts_rank_cd order by coverage.
_SPARSE_SQL = f"""
    WITH q AS (
        SELECT CAST(
            replace(CAST(plainto_tsquery('english', :q) AS text), '&', '|') AS tsquery
        ) AS query
    )
    SELECT c.id, c.document_id, d.title, c.page_number, c.content,
           ts_rank_cd(c.content_tsv, q.query) AS score
    FROM q, chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.content_tsv @@ q.query AND d.status = 'ready' AND {_COURSE_FILTER}
    ORDER BY score DESC, c.document_id, c.chunk_index
    LIMIT :k
"""  # noqa: S608 - only module constants are interpolated


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
        self._dense_sql = text(_DENSE_SQL).bindparams(bindparam("qvec", type_=Vector(embedder.dim)))
        self._sparse_sql = text(_SPARSE_SQL)
        # Transaction-local, so it never leaks to other users of the pooled connection.
        self._iterative_scan_sql = text("SELECT set_config('hnsw.iterative_scan', :mode, true)")

    async def retrieve(
        self,
        session: AsyncSession,
        query: str,
        *,
        course_code: str | None = None,
    ) -> list[RetrievedChunk]:
        with tracer.start_as_current_span("rag.embed"), observe_stage("embed"):
            query_vec = await self._embedder.embed_one(query)

        with (
            tracer.start_as_current_span("rag.retrieve") as span,
            observe_stage("retrieve"),
        ):
            dense, sparse = await self._dense_and_sparse(
                session, query=query, query_vec=query_vec, course_code=course_code
            )
            fused = self._rrf(dense, sparse, k=self._settings.rrf_k)
            span.set_attribute("rag.dense_hits", len(dense))
            span.set_attribute("rag.sparse_hits", len(sparse))
            span.set_attribute("rag.fused_hits", len(fused))

        RAG_RETRIEVED_CHUNKS.observe(len(fused))
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
        course = course_code or None
        if self._settings.hnsw_iterative_scan != "off":
            await session.execute(
                self._iterative_scan_sql, {"mode": self._settings.hnsw_iterative_scan}
            )
        dense_rows = (
            await session.execute(
                self._dense_sql,
                {"qvec": query_vec, "course_code": course, "k": self._settings.dense_top_k},
            )
        ).all()
        sparse_rows = (
            await session.execute(
                self._sparse_sql,
                {"q": query, "course_code": course, "k": self._settings.sparse_top_k},
            )
        ).all()
        return [self._row(r) for r in dense_rows], [self._row(r) for r in sparse_rows]

    @staticmethod
    def _row(row: Any) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=row.id,
            document_id=row.document_id,
            document_title=row.title,
            page_number=row.page_number,
            content=row.content,
            score=float(row.score),
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

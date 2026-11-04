"""Retrieval tuning experiments for ``python -m eval.retrieval_eval --sweep``.

Varies one knob at a time around the production settings and reports overall
ranking metrics on the in-scope questions:

- candidate depth: dense/sparse top-k, with and without rerank;
- RRF k (only affects the un-reranked fused order: rerank re-scores the whole pool);
- sparse query mode: production ``_dense_and_sparse`` versus the original
  ``plainto_tsquery`` query, which ANDs every term (kept here as a baseline).

Cross-encoder scores depend only on (query, passage), so they are cached per
question and reused across variants.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.rag.embeddings import Embedder
from app.rag.reranker import Reranker
from app.rag.retriever import HybridRetriever, RetrievedChunk
from eval.common import CorpusIndex, fmt, md_table
from eval.dataset import RANKED_CATEGORIES
from eval.metrics import RANKING_METRICS, mean, ranking_scores

if TYPE_CHECKING:
    from eval.retrieval_eval import QuestionRun

DEPTHS = (5, 10, 20, 40)
RRF_KS = (1, 10, 30, 60, 100)

# Baseline: the original sparse query. plainto_tsquery ANDs all query terms, so
# a chunk must contain every non-stopword of the question to match at all.
AND_SPARSE_SQL = text(
    """
    SELECT c.id, c.document_id, d.title, c.page_number, c.content,
           ts_rank_cd(c.content_tsv, plainto_tsquery('english', :q)) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.content_tsv @@ plainto_tsquery('english', :q) AND d.status = 'ready'
    ORDER BY score DESC, c.id
    LIMIT :k
    """
)


class RerankCache:
    """Cross-encoder scores per (question, chunk), computed at most once."""

    def __init__(self, reranker: Reranker, enabled: bool) -> None:
        self._reranker = reranker
        self._enabled = enabled
        self._scores: dict[tuple[str, UUID], float] = {}

    async def rank(self, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        if not self._enabled:  # RERANKER_PROVIDER=none keeps the fused order
            return chunks
        missing = [c for c in chunks if (query, c.chunk_id) not in self._scores]
        if missing:
            for c in await self._reranker.rerank(query, missing, top_n=len(missing)):
                self._scores[(query, c.chunk_id)] = c.score
        return sorted(chunks, key=lambda c: self._scores[(query, c.chunk_id)], reverse=True)


async def _and_sparse(session: AsyncSession, query: str, k: int) -> list[RetrievedChunk]:
    rows = (await session.execute(AND_SPARSE_SQL, {"q": query, "k": k})).all()
    return [
        RetrievedChunk(
            chunk_id=r.id,
            document_id=r.document_id,
            document_title=r.title,
            page_number=r.page_number,
            content=r.content,
            score=float(r.score),
        )
        for r in rows
    ]


def _summarise(
    runs: list[QuestionRun],
    rankings: list[list[RetrievedChunk]],
    index: CorpusIndex,
    *,
    by_category: bool = False,
) -> dict[str, Any]:
    """Mean metrics of one variant; ``rankings`` is aligned with the in-scope ``runs``."""
    from eval.retrieval_eval import section_ranking

    per_q = [
        (
            r.question.category,
            ranking_scores(section_ranking(chunks, index), set(r.question.relevant)),
        )
        for r, chunks in zip(runs, rankings, strict=True)
    ]
    out: dict[str, Any] = {m: mean([s[m] for _, s in per_q]) for m in RANKING_METRICS}
    if by_category:
        out["by_category"] = {
            cat: {m: mean([s[m] for c, s in per_q if c == cat]) for m in RANKING_METRICS}
            for cat in RANKED_CATEGORIES
        }
    return out


async def run_sweep(
    runs: list[QuestionRun],
    *,
    session: AsyncSession,
    embedder: Embedder,
    reranker: Reranker,
    settings: Settings,
    index: CorpusIndex,
) -> tuple[dict[str, Any], list[str]]:
    in_scope = [r for r in runs if r.question.in_scope]
    cache = RerankCache(reranker, enabled=settings.reranker_provider != "none")
    deep = HybridRetriever(
        embedder=embedder,
        settings=settings.model_copy(
            update={"dense_top_k": max(DEPTHS), "sparse_top_k": max(DEPTHS)}
        ),
    )
    prod_depth = (settings.dense_top_k, settings.sparse_top_k)

    deep_lists: list[tuple[list[RetrievedChunk], list[RetrievedChunk], list[RetrievedChunk]]] = []
    for r in in_scope:
        dense, sparse = await deep._dense_and_sparse(
            session, query=r.question.question, query_vec=r.retrieved.query_vec, course_code=None
        )
        and_sparse = await _and_sparse(session, r.question.question, max(DEPTHS))
        deep_lists.append((dense, sparse, and_sparse))

    async def variant(
        build: Callable[[int], list[RetrievedChunk]], *, rerank: bool
    ) -> list[list[RetrievedChunk]]:
        out = []
        for i, r in enumerate(in_scope):
            chunks = build(i)
            out.append(await cache.rank(r.question.question, chunks) if rerank else chunks)
        return out

    rrf = HybridRetriever._rrf

    # 1. Candidate depth (production FTS mode and RRF k).
    depth_rows: list[dict[str, Any]] = []
    for d in DEPTHS:

        def fused(i: int, d: int = d) -> list[RetrievedChunk]:
            dense, sparse, _ = deep_lists[i]
            return rrf(dense[:d], sparse[:d], k=settings.rrf_k)

        depth_rows.append(
            {
                "top_k": d,
                "pool_mean": mean([float(len(fused(i))) for i in range(len(in_scope))]),
                "hybrid": _summarise(in_scope, await variant(fused, rerank=False), index),
                "hybrid_rerank": _summarise(in_scope, await variant(fused, rerank=True), index),
            }
        )

    # 2. RRF k at production depth (no rerank: rerank re-scores the full pool).
    k_rows: list[dict[str, Any]] = []
    for k in RRF_KS:

        def fused_k(i: int, k: int = k) -> list[RetrievedChunk]:
            dense, sparse, _ = deep_lists[i]
            return rrf(dense[: prod_depth[0]], sparse[: prod_depth[1]], k=k)

        k_rows.append(
            {
                "rrf_k": k,
                "hybrid": _summarise(in_scope, await variant(fused_k, rerank=False), index),
            }
        )

    # 3. Sparse query mode at production depth.
    mode_rows: list[dict[str, Any]] = []
    for mode, col in (("production", 1), ("plainto_tsquery AND (original)", 2)):

        def sparse_only(i: int, col: int = col) -> list[RetrievedChunk]:
            return deep_lists[i][col][: prod_depth[1]]

        def fused_mode(i: int, col: int = col) -> list[RetrievedChunk]:
            return rrf(deep_lists[i][0][: prod_depth[0]], sparse_only(i), k=settings.rrf_k)

        mode_rows.append(
            {
                "mode": mode,
                "sparse_empty": sum(1 for i in range(len(in_scope)) if not sparse_only(i)),
                "sparse": _summarise(
                    in_scope, await variant(sparse_only, rerank=False), index, by_category=True
                ),
                "hybrid": _summarise(
                    in_scope, await variant(fused_mode, rerank=False), index, by_category=True
                ),
                "hybrid_rerank": _summarise(
                    in_scope, await variant(fused_mode, rerank=True), index, by_category=True
                ),
            }
        )

    result = {"depth": depth_rows, "rrf_k": k_rows, "sparse_mode": mode_rows}
    return result, _markdown(result, settings)


def _cells(m: dict[str, Any]) -> list[str]:
    return [fmt(m[x]) for x in RANKING_METRICS]


def _markdown(result: dict[str, Any], settings: Settings) -> list[str]:
    lines = [
        "## Sweep",
        "",
        "One knob at a time around production settings; in-scope questions; overall means.",
        "",
        f"### Candidate depth (dense/sparse top-k; production {settings.dense_top_k}/"
        f"{settings.sparse_top_k})",
        "",
    ]
    rows = []
    for row in result["depth"]:
        for cfg in ("hybrid", "hybrid_rerank"):
            rows.append([str(row["top_k"]), f"{row['pool_mean']:.1f}", cfg, *_cells(row[cfg])])
    lines += md_table(["top-k", "pool", "config", *RANKING_METRICS], rows)

    lines += ["", f"### RRF k (hybrid without rerank; production k={settings.rrf_k})", ""]
    rows = [[str(row["rrf_k"]), *_cells(row["hybrid"])] for row in result["rrf_k"]]
    lines += md_table(["k", *RANKING_METRICS], rows)

    lines += ["", "### Sparse query mode", ""]
    rows = []
    for row in result["sparse_mode"]:
        for cfg in ("sparse", "hybrid", "hybrid_rerank"):
            rows.append([row["mode"], str(row["sparse_empty"]), cfg, *_cells(row[cfg])])
    lines += md_table(["FTS query", "no FTS match", "config", *RANKING_METRICS], rows)

    lines += ["", "MRR@10 by category for each sparse query mode:", ""]
    rows = []
    for row in result["sparse_mode"]:
        for cfg in ("sparse", "hybrid", "hybrid_rerank"):
            cats = row[cfg]["by_category"]
            rows.append([row["mode"], cfg, *(fmt(cats[c]["mrr@10"]) for c in RANKED_CATEGORIES)])
    lines += md_table(["FTS query", "config", *RANKED_CATEGORIES], rows)
    return lines

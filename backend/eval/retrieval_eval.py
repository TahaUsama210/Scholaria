"""Retrieval eval: dense vs sparse vs hybrid RRF vs hybrid RRF + cross-encoder rerank.

Run from ``backend/`` against a database seeded with ``python -m scripts.seed_demo_docs``::

    python -m eval.retrieval_eval            # writes eval_results/retrieval.{json,md}
    python -m eval.retrieval_eval --sweep    # + candidate depth / RRF k / FTS experiments

All four configurations rank the *same* candidate pool: the dense and sparse
lists produced by ``HybridRetriever._dense_and_sparse`` with the production
settings. Relevance is per section (course, page); chunks are mapped to their
section and de-duplicated before scoring. Exits 1 when the production ranking
(hybrid + rerank) falls below the matching profile in ``eval/thresholds.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import async_session_factory
from app.rag.embeddings import Embedder, make_embedder
from app.rag.reranker import Reranker, make_reranker
from app.rag.retriever import HybridRetriever, RetrievedChunk
from eval.common import (
    DEFAULT_OUT_DIR,
    CorpusIndex,
    CorpusNotSeededError,
    clean_floats,
    fmt,
    gate_lines,
    load_corpus_index,
    load_profiles,
    md_table,
    provider_label,
    run_gate,
    select_profile,
    settings_snapshot,
    utc_now,
    write_report,
)
from eval.dataset import (
    RANKED_CATEGORIES,
    GoldenQuestion,
    SectionRef,
    load_dataset,
    validate_against_corpus,
)
from eval.metrics import (
    RANKING_METRICS,
    dedupe,
    first_relevant_rank,
    mean,
    paired_bootstrap_ci,
    percentile,
    ranking_scores,
)
from scripts.demo_corpus import load_corpus

CONFIGS = ("dense", "sparse", "hybrid", "hybrid_rerank")
CONFIG_LABELS = {
    "dense": "Dense only (pgvector cosine)",
    "sparse": "Sparse only (Postgres FTS)",
    "hybrid": "Hybrid (RRF)",
    "hybrid_rerank": "Hybrid (RRF) + cross-encoder rerank",
}
PRODUCTION_CONFIG = "hybrid_rerank"
DELTA_PAIRS = (("dense", "hybrid"), ("dense", "hybrid_rerank"), ("hybrid", "hybrid_rerank"))
# Candidate floors on the top cross-encoder score for a "no relevant context" gate.
SCORE_FLOORS = (-10.0, -9.0, -8.0, -7.0, -6.0, -5.0, -4.0)
SETTINGS_KEYS = (
    "embedding_provider",
    "embedding_model",
    "embedding_dim",
    "reranker_provider",
    "reranker_model",
    "dense_top_k",
    "sparse_top_k",
    "rrf_k",
    "rerank_top_n",
    "chunk_size_tokens",
    "chunk_overlap_tokens",
)

Scores = dict[str, dict[str, float]]  # config -> metric -> value


@dataclass(slots=True)
class Retrieved:
    query_vec: list[float]
    dense: list[RetrievedChunk]
    sparse: list[RetrievedChunk]
    hybrid: list[RetrievedChunk]
    reranked: list[RetrievedChunk]
    timings_ms: dict[str, float]

    def ranking(self, config: str) -> list[RetrievedChunk]:
        return {
            "dense": self.dense,
            "sparse": self.sparse,
            "hybrid": self.hybrid,
            "hybrid_rerank": self.reranked,
        }[config]


@dataclass(slots=True)
class QuestionRun:
    question: GoldenQuestion
    retrieved: Retrieved
    rankings: dict[str, list[SectionRef]]
    scores: Scores  # empty for out_of_scope


def section_ranking(chunks: list[RetrievedChunk], index: CorpusIndex) -> list[SectionRef]:
    return dedupe(index.section(c.document_id, c.page_number) for c in chunks)


async def retrieve(
    question: str,
    *,
    session: AsyncSession,
    retriever: HybridRetriever,
    embedder: Embedder,
    reranker: Reranker,
    settings: Settings,
) -> Retrieved:
    t0 = time.perf_counter()
    query_vec = await embedder.embed_one(question)
    t1 = time.perf_counter()
    dense, sparse = await retriever._dense_and_sparse(
        session, query=question, query_vec=query_vec, course_code=None
    )
    t2 = time.perf_counter()
    hybrid = HybridRetriever._rrf(dense, sparse, k=settings.rrf_k)
    # Rank the whole fused pool (production keeps the first rerank_top_n of it).
    reranked = await reranker.rerank(question, hybrid, top_n=len(hybrid)) if hybrid else []
    t3 = time.perf_counter()
    return Retrieved(
        query_vec=query_vec,
        dense=dense,
        sparse=sparse,
        hybrid=hybrid,
        reranked=reranked,
        timings_ms={
            "embed": (t1 - t0) * 1000,
            "dense_and_sparse_sql": (t2 - t1) * 1000,
            "rrf_and_rerank": (t3 - t2) * 1000,
        },
    )


def score_rankings(q: GoldenQuestion, rankings: dict[str, list[SectionRef]]) -> Scores:
    if not q.in_scope:
        return {}
    relevant = set(q.relevant)
    return {cfg: ranking_scores(ranked, relevant) for cfg, ranked in rankings.items()}


def aggregate(
    pairs: list[tuple[GoldenQuestion, Scores]], configs: tuple[str, ...]
) -> dict[str, Any]:
    """Mean of each metric per config, overall and per ranked category."""
    scored = [(q, s) for q, s in pairs if s]

    def means(rows: list[Scores]) -> dict[str, dict[str, float]]:
        return {
            cfg: {m: mean([r[cfg][m] for r in rows]) for m in RANKING_METRICS} for cfg in configs
        }

    by_category = {
        cat: means([s for q, s in scored if q.category == cat])
        for cat in RANKED_CATEGORIES
        if any(q.category == cat for q, _ in scored)
    }
    counts = Counter(q.category for q, _ in scored)
    return {
        "n": len(scored),
        "n_by_category": dict(counts),
        "overall": means([s for _, s in scored]),
        "by_category": by_category,
    }


def deltas(pairs: list[tuple[GoldenQuestion, Scores]]) -> list[dict[str, Any]]:
    scored = [s for _, s in pairs if s]
    out: list[dict[str, Any]] = []
    for a, b in DELTA_PAIRS:
        for metric in RANKING_METRICS:
            delta, lo, hi = paired_bootstrap_ci(
                [s[a][metric] for s in scored], [s[b][metric] for s in scored]
            )
            out.append({"from": a, "to": b, "metric": metric, "delta": delta, "ci95": [lo, hi]})
    return out


def diagnostics(runs: list[QuestionRun], settings: Settings) -> dict[str, Any]:
    in_scope = [r for r in runs if r.question.in_scope]
    oos = [r for r in runs if not r.question.in_scope]
    sparse_empty = Counter(r.question.category for r in runs if not r.retrieved.sparse)
    out: dict[str, Any] = {
        "sparse_empty_by_category": dict(sparse_empty),
        "sparse_hits_mean": mean([float(len(r.retrieved.sparse)) for r in in_scope]),
        "candidate_pool_mean": mean([float(len(r.retrieved.hybrid)) for r in runs]),
        "latency_ms": {
            stage: {
                "mean": mean([r.retrieved.timings_ms[stage] for r in runs]),
                "p95": percentile([r.retrieved.timings_ms[stage] for r in runs], 95),
            }
            for stage in runs[0].retrieved.timings_ms
        },
    }
    if settings.reranker_provider != "none":
        # How separable are answerable and unanswerable questions by top rerank score?
        top_in = [r.retrieved.reranked[0].score for r in in_scope if r.retrieved.reranked]
        top_oos = [r.retrieved.reranked[0].score for r in oos if r.retrieved.reranked]
        out["top_rerank_score"] = {
            "in_scope": {
                "min": min(top_in, default=float("nan")),
                "median": percentile(top_in, 50),
            },
            "out_of_scope": {
                "max": max(top_oos, default=float("nan")),
                "median": percentile(top_oos, 50),
            },
        }
        out["score_floor"] = [
            {
                "floor": floor,
                "out_of_scope_rejected": sum(1 for x in top_oos if x < floor),
                "in_scope_rejected": sum(1 for x in top_in if x < floor),
            }
            for floor in SCORE_FLOORS
        ]
    return out


def question_records(runs: list[QuestionRun]) -> list[dict[str, Any]]:
    return [
        {
            "id": r.question.id,
            "category": r.question.category,
            "relevant": [str(x) for x in r.question.relevant],
            "sparse_hits": len(r.retrieved.sparse),
            "first_relevant_rank": {
                cfg: first_relevant_rank(ranked, set(r.question.relevant))
                for cfg, ranked in r.rankings.items()
            }
            if r.question.in_scope
            else None,
            "top5": {cfg: [str(x) for x in ranked[:5]] for cfg, ranked in r.rankings.items()},
            "top_rerank_score": r.retrieved.reranked[0].score if r.retrieved.reranked else None,
        }
        for r in runs
    ]


# --- markdown -------------------------------------------------------------------


def render_markdown(report: dict[str, Any], gate_md: list[str], sweep_md: list[str]) -> str:
    agg = report["results"]
    s = report["settings"]
    lines = [
        "# Retrieval eval",
        "",
        f"Generated {report['generated_at']} - dataset v{report['dataset_version']}, "
        f"{agg['n']} ranked questions ({report['n_out_of_scope']} out-of-scope excluded), "
        f"{report['corpus']['chunks']} chunks (mean {report['corpus']['chunk_tokens_mean']:.0f}, "
        f"max {report['corpus']['chunk_tokens_max']} tokens as ingested).",
        "",
        f"Embeddings `{provider_label(s['embedding_provider'], s['embedding_model'])}` "
        f"({s['embedding_dim']}-d), "
        f"reranker `{provider_label(s['reranker_provider'], s['reranker_model'])}`, "
        f"dense/sparse top-k {s['dense_top_k']}/{s['sparse_top_k']}, RRF k={s['rrf_k']}.",
        "",
        "## Overall",
        "",
    ]
    rows = [
        [CONFIG_LABELS[cfg]] + [fmt(agg["overall"][cfg][m]) for m in RANKING_METRICS]
        for cfg in CONFIGS
    ]
    lines += md_table(["config", *RANKING_METRICS], rows)

    lines += ["", "## By category", ""]
    rows = [
        [f"{cat} (n={agg['n_by_category'][cat]})", cfg]
        + [fmt(agg["by_category"][cat][cfg][m]) for m in RANKING_METRICS]
        for cat in agg["by_category"]
        for cfg in CONFIGS
    ]
    lines += md_table(["category", "config", *RANKING_METRICS], rows)

    lines += [
        "",
        "## Paired differences (95% bootstrap CI over questions)",
        "",
    ]
    rows = [
        [
            f"{d['from']} -> {d['to']}",
            d["metric"],
            f"{d['delta']:+.3f}",
            f"[{d['ci95'][0]:+.3f}, {d['ci95'][1]:+.3f}]",
        ]
        for d in report["deltas"]
    ]
    lines += md_table(["comparison", "metric", "delta", "95% CI"], rows)

    diag = report["diagnostics"]
    empty = diag["sparse_empty_by_category"]
    lines += [
        "",
        "## Diagnostics",
        "",
        f"- Questions where FTS matched nothing: {sum(empty.values())} "
        f"({', '.join(f'{k}: {v}' for k, v in sorted(empty.items())) or 'none'}); "
        f"mean FTS hits for in-scope questions {diag['sparse_hits_mean']:.1f}.",
        f"- Mean fused candidate pool: {diag['candidate_pool_mean']:.1f} chunks.",
    ]
    for stage, v in diag["latency_ms"].items():
        lines.append(f"- Latency `{stage}`: mean {v['mean']:.1f} ms, p95 {v['p95']:.1f} ms.")
    if "top_rerank_score" in diag:
        t = diag["top_rerank_score"]
        lines.append(
            f"- Top rerank score: in-scope min {fmt(t['in_scope']['min'], 2)}, "
            f"median {fmt(t['in_scope']['median'], 2)}; out-of-scope max "
            f"{fmt(t['out_of_scope']['max'], 2)}, median {fmt(t['out_of_scope']['median'], 2)}."
        )
        lines += [
            "",
            "Refusing when the top rerank score is below a floor (in-scope n="
            f"{agg['n']}, out-of-scope n={report['n_out_of_scope']}):",
            "",
        ]
        rows = [
            [f"{f['floor']:.1f}", str(f["out_of_scope_rejected"]), str(f["in_scope_rejected"])]
            for f in diag["score_floor"]
        ]
        lines += md_table(["floor", "out-of-scope refused", "in-scope refused"], rows)

    misses = [
        q
        for q in report["questions"]
        if q["first_relevant_rank"] and q["first_relevant_rank"][PRODUCTION_CONFIG] != 1
    ]
    lines += ["", f"## Production misses at rank 1 ({len(misses)})", ""]
    rows = [
        [
            q["id"],
            q["category"],
            ", ".join(q["relevant"]),
            str(q["first_relevant_rank"][PRODUCTION_CONFIG] or "-"),
            (q["top5"][PRODUCTION_CONFIG] or ["-"])[0],
        ]
        for q in misses
    ]
    lines += md_table(["question", "category", "relevant", "rank", "top-1"], rows)
    lines += ["", "## Gate", "", *gate_md]
    if sweep_md:
        lines += ["", *sweep_md]
    return "\n".join(lines)


# --- main -----------------------------------------------------------------------


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    dataset = load_dataset()
    corpus = load_corpus()
    if problems := validate_against_corpus(dataset, corpus):
        print("golden dataset is invalid:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2

    embedder = make_embedder(settings)
    reranker = make_reranker(settings)
    retriever = HybridRetriever(embedder=embedder, settings=settings)
    # Warm up model weights / kernels so per-stage latency reflects steady state.
    await embedder.embed_one("warm up")
    warm = RetrievedChunk(uuid4(), uuid4(), "warm-up", 1, "warm up", 0.0)
    await reranker.rerank("warm up", [warm], top_n=1)

    async with async_session_factory() as session:
        try:
            index = await load_corpus_index(session, corpus)
        except CorpusNotSeededError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if index.extra_documents:
            print(f"warning: {index.extra_documents} non-corpus document(s) are indexed")

        runs: list[QuestionRun] = []
        for q in dataset.questions:
            retrieved = await retrieve(
                q.question,
                session=session,
                retriever=retriever,
                embedder=embedder,
                reranker=reranker,
                settings=settings,
            )
            rankings = {cfg: section_ranking(retrieved.ranking(cfg), index) for cfg in CONFIGS}
            runs.append(QuestionRun(q, retrieved, rankings, score_rankings(q, rankings)))

        sweep: dict[str, Any] | None = None
        sweep_md: list[str] = []
        if args.sweep:
            from eval.retrieval_sweep import run_sweep

            sweep, sweep_md = await run_sweep(
                runs, session=session, embedder=embedder, reranker=reranker,
                settings=settings, index=index,
            )  # fmt: skip

    pairs = [(r.question, r.scores) for r in runs]
    results = aggregate(pairs, CONFIGS)
    profile = None if args.no_gate else select_profile(
        load_profiles("retrieval"), settings, name=args.profile
    )  # fmt: skip
    gate = run_gate(results["overall"][PRODUCTION_CONFIG], profile)

    report: dict[str, Any] = {
        "generated_at": utc_now(),
        "dataset_version": dataset.version,
        "n_questions": len(dataset.questions),
        "n_out_of_scope": sum(1 for q in dataset.questions if not q.in_scope),
        "settings": settings_snapshot(settings, SETTINGS_KEYS),
        "corpus": {
            "documents": len(index.doc_course),
            "chunks": index.chunk_count,
            "extra_documents": index.extra_documents,
            "chunk_tokens_mean": index.chunk_tokens_mean,
            "chunk_tokens_max": index.chunk_tokens_max,
        },
        "production_config": PRODUCTION_CONFIG,
        "results": results,
        "deltas": deltas(pairs),
        "diagnostics": diagnostics(runs, settings),
        "gate": gate.as_dict(),
        "sweep": sweep,
        "questions": question_records(runs),
    }
    report = clean_floats(report)
    md_path = write_report(
        args.out, "retrieval", report, render_markdown(report, gate_lines(gate), sweep_md)
    )

    print(f"{'config':<16}" + "".join(f"{m:>10}" for m in RANKING_METRICS))
    for cfg in CONFIGS:
        vals = results["overall"][cfg]
        print(f"{cfg:<16}" + "".join(f"{vals[m]:>10.3f}" for m in RANKING_METRICS))
    print("\n".join(gate_lines(gate)))
    print(f"report: {md_path}")
    return 0 if gate.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval configurations.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="report directory")
    parser.add_argument("--sweep", action="store_true", help="also run tuning experiments")
    parser.add_argument("--profile", help="threshold profile to gate against (default: auto)")
    parser.add_argument("--no-gate", action="store_true", help="report only, always exit 0")
    args = parser.parse_args(argv)
    configure_logging(get_settings().log_level)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())

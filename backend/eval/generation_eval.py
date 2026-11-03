"""Generation eval: every golden question through the full pipeline (``RagPipeline.stream``).

Run from ``backend/`` against a seeded database::

    python -m eval.generation_eval        # writes eval_results/generation.{json,md}

Deterministic checks (always on, also with ``LLM_PROVIDER=fake``):

- non_empty_rate: answers with visible text
- citation_validity: answers whose every [n] points at a passage in the context
- citation_coverage: share of claim sentences carrying a citation (in-scope answers)
- context_recall: share of labelled sections present in the generator's context
- cited_relevant_rate: in-scope answers citing at least one labelled section
- refusal_accuracy / false_refusal_rate: refusals on out-of-scope / in-scope questions
- error_rate: questions whose stream raised or emitted an ``error`` event

The LLM-as-judge (faithfulness, correctness) runs only with a real LLM; see
``eval/judge.py``. Exits 1 when a gated metric misses ``eval/thresholds.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import async_session_factory
from app.rag.pipeline import RagPipeline, get_pipeline
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
from eval.dataset import CATEGORIES, GoldenQuestion, SectionRef, load_dataset
from eval.judge import Judge, JudgeScore, judge_settings_status
from eval.metrics import (
    citation_coverage,
    citations_valid,
    cited_markers,
    is_refusal,
    mean,
    percentile,
)
from scripts.demo_corpus import load_corpus

SETTINGS_KEYS = (
    "llm_provider",
    "llm_model",
    "llm_base_url",
    "embedding_provider",
    "embedding_model",
    "embedding_dim",
    "reranker_provider",
    "reranker_model",
    "rerank_top_n",
    "rerank_min_score",
)
METRICS = (
    "non_empty_rate",
    "citation_validity",
    "citation_coverage",
    "context_recall",
    "cited_relevant_rate",
    "refusal_accuracy",
    "false_refusal_rate",
    "error_rate",
)
JUDGE_METRICS = ("judge_faithfulness", "judge_correctness")


@dataclass(slots=True)
class Record:
    question: GoldenQuestion
    answer: str = ""
    context: list[dict[str, Any]] = field(default_factory=list)
    outcome: str | None = None
    error: str | None = None
    ttft_ms: float | None = None
    total_ms: float = 0.0
    context_sections: list[SectionRef] = field(default_factory=list)
    faithfulness: JudgeScore | None = None
    correctness: JudgeScore | None = None

    @property
    def refused(self) -> bool:
        return is_refusal(self.answer)

    @property
    def answered(self) -> bool:
        """Produced a non-refusal answer without error."""
        return self.error is None and bool(self.answer.strip()) and not self.refused

    def cited_sections(self) -> set[SectionRef]:
        n = len(self.context_sections)
        return {self.context_sections[m - 1] for m in cited_markers(self.answer) if 1 <= m <= n}


async def run_question(pipeline: RagPipeline, q: GoldenQuestion, index: CorpusIndex) -> Record:
    rec = Record(question=q)
    tokens: list[str] = []
    start = time.perf_counter()
    try:
        async with async_session_factory() as session:
            async for ev in pipeline.stream(session, question=q.question):
                if ev.type == "context" and isinstance(ev.data, list):
                    rec.context = ev.data
                elif ev.type == "token":
                    if rec.ttft_ms is None:
                        rec.ttft_ms = (time.perf_counter() - start) * 1000
                    tokens.append(str(ev.data))
                elif ev.type == "done" and isinstance(ev.data, dict):
                    rec.outcome = str(ev.data.get("outcome", "")) or None
                    if not tokens:
                        tokens.append(str(ev.data.get("answer", "")))
                elif ev.type == "error":
                    rec.error = f"error event: {ev.data}"
    except Exception as exc:  # recorded as a failed question, the run continues
        rec.error = f"{type(exc).__name__}: {exc}"[:300]
    rec.total_ms = (time.perf_counter() - start) * 1000
    rec.answer = "".join(tokens)
    rec.context_sections = [
        index.section(UUID(str(c["document_id"])), c.get("page_number")) for c in rec.context
    ]
    return rec


async def fetch_passages(session: AsyncSession, context: list[dict[str, Any]]) -> list[str]:
    """Full chunk text for the context (SSE events only carry a snippet)."""
    ids = [UUID(str(c["chunk_id"])) for c in context]
    if not ids:
        return []
    rows = (
        await session.execute(
            text("SELECT id, content FROM chunks WHERE id = ANY(:ids)"), {"ids": ids}
        )
    ).all()
    content = {r.id: r.content for r in rows}
    return [content.get(i, "") for i in ids]


async def run_judge(judge: Judge, records: list[Record]) -> None:
    async with async_session_factory() as session:
        for rec in records:
            if not rec.question.in_scope or not rec.answered:
                continue
            passages = await fetch_passages(session, rec.context)
            rec.faithfulness = await judge.faithfulness(
                question=rec.question.question, answer=rec.answer, passages=passages
            )
            rec.correctness = await judge.correctness(
                question=rec.question.question,
                answer=rec.answer,
                reference=rec.question.reference_answer,
            )


def summarise(records: list[Record]) -> dict[str, float]:
    in_scope = [r for r in records if r.question.in_scope]
    oos = [r for r in records if not r.question.in_scope]
    ok = [r for r in records if r.error is None]
    answered = [r for r in in_scope if r.answered]
    coverage = [c for r in answered if (c := citation_coverage(r.answer)) is not None]
    out = {
        "non_empty_rate": mean([1.0 if r.answer.strip() else 0.0 for r in records]),
        "citation_validity": mean(
            [1.0 if citations_valid(r.answer, len(r.context)) else 0.0 for r in ok]
        ),
        "citation_coverage": mean(coverage),
        "context_recall": mean(
            [
                len(set(r.question.relevant) & set(r.context_sections)) / len(r.question.relevant)
                for r in in_scope
            ]
        ),
        "cited_relevant_rate": mean(
            [1.0 if r.cited_sections() & set(r.question.relevant) else 0.0 for r in answered]
        ),
        "refusal_accuracy": mean([1.0 if r.refused else 0.0 for r in oos]),
        "false_refusal_rate": mean([1.0 if r.refused else 0.0 for r in in_scope]),
        "error_rate": mean([0.0 if r.error is None else 1.0 for r in records]),
    }
    for name, attr in (
        ("judge_faithfulness", "faithfulness"),
        ("judge_correctness", "correctness"),
    ):
        scores = [s.score for r in records if (s := getattr(r, attr)) and s.score is not None]
        if scores:
            out[name] = mean(scores)
    return out


def record_dicts(records: list[Record]) -> list[dict[str, Any]]:
    def judged(s: JudgeScore | None) -> dict[str, Any] | None:
        if s is None:
            return None
        return {"score": s.score, "raw": s.raw, "reason": s.reason, "error": s.error}

    return [
        {
            "id": r.question.id,
            "category": r.question.category,
            "question": r.question.question,
            "relevant": [str(x) for x in r.question.relevant],
            "answer": r.answer,
            "outcome": r.outcome,
            "error": r.error,
            "refused": r.refused,
            "context_sections": [str(x) for x in r.context_sections],
            "cited_markers": cited_markers(r.answer),
            "citations_valid": citations_valid(r.answer, len(r.context)),
            "citation_coverage": citation_coverage(r.answer),
            "ttft_ms": r.ttft_ms,
            "total_ms": r.total_ms,
            "judge_faithfulness": judged(r.faithfulness),
            "judge_correctness": judged(r.correctness),
        }
        for r in records
    ]


def render_markdown(report: dict[str, Any], gate_md: list[str]) -> str:
    s = report["settings"]
    overall = report["results"]["overall"]
    lines = [
        "# Generation eval",
        "",
        f"Generated {report['generated_at']} - dataset v{report['dataset_version']}, "
        f"{report['n_questions']} questions through `RagPipeline.stream`.",
        "",
        f"LLM `{provider_label(s['llm_provider'], s['llm_model'])}`, embeddings "
        f"`{provider_label(s['embedding_provider'], s['embedding_model'])}`, "
        f"reranker `{provider_label(s['reranker_provider'], s['reranker_model'])}`, "
        f"context top-n {s['rerank_top_n']}.",
        "",
        f"LLM-as-judge: {report['judge']['status']}.",
        "",
        "## Overall",
        "",
    ]
    names = [m for m in (*METRICS, *JUDGE_METRICS) if m in overall]
    lines += md_table(["metric", "value"], [[m, fmt(overall[m])] for m in names])

    lines += ["", "## By category", ""]
    by_cat = report["results"]["by_category"]
    rows = [[cat, *(fmt(by_cat[cat].get(m)) for m in names)] for cat in by_cat]
    lines += md_table(["category", *names], rows)

    lat = report["latency_ms"]
    lines += [
        "",
        f"Latency: time to first token p50 {fmt(lat['ttft_p50'], 0)} ms, "
        f"p95 {fmt(lat['ttft_p95'], 0)} ms; total p50 {fmt(lat['total_p50'], 0)} ms, "
        f"p95 {fmt(lat['total_p95'], 0)} ms.",
    ]
    problems = [
        r
        for r in report["questions"]
        if r["error"]
        or not r["citations_valid"]
        or (r["category"] == "out_of_scope") != r["refused"]
    ]
    lines += ["", f"## Questions to inspect ({len(problems)})", ""]
    rows = [
        [
            r["id"],
            r["category"],
            "yes" if r["refused"] else "no",
            "yes" if r["citations_valid"] else "NO",
            (r["error"] or r["answer"][:90].replace("|", "/").replace("\n", " ")),
        ]
        for r in problems
    ]
    lines += md_table(["question", "category", "refused", "citations valid", "answer"], rows)
    lines += ["", "## Gate", "", *gate_md]
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    settings: Settings = get_settings()
    dataset = load_dataset()
    questions = list(dataset.questions)[: args.limit] if args.limit else list(dataset.questions)
    corpus = load_corpus()

    async with async_session_factory() as session:
        try:
            index = await load_corpus_index(session, corpus)
        except CorpusNotSeededError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    pipeline = get_pipeline()
    records = [await run_question(pipeline, q, index) for q in questions]

    judge_on, judge_status = judge_settings_status(settings)
    if args.no_judge:
        judge_on, judge_status = False, "disabled with --no-judge"
    if judge_on:
        judge = Judge.from_settings(settings)
        judge_status = f"model `{judge.model}`"
        await run_judge(judge, records)
    else:
        print(f"LLM-as-judge skipped: {judge_status}")

    by_category = {
        cat: summarise([r for r in records if r.question.category == cat])
        for cat in CATEGORIES
        if any(r.question.category == cat for r in records)
    }
    overall = summarise(records)
    profile = (
        None
        if args.no_gate
        else select_profile(load_profiles("generation"), settings, name=args.profile)
    )
    gate = run_gate(overall, profile)
    ttft = [r.ttft_ms for r in records if r.ttft_ms is not None]
    total = [r.total_ms for r in records]

    report: dict[str, Any] = {
        "generated_at": utc_now(),
        "dataset_version": dataset.version,
        "n_questions": len(records),
        "settings": settings_snapshot(settings, SETTINGS_KEYS),
        "judge": {"enabled": judge_on, "status": judge_status},
        "results": {"overall": overall, "by_category": by_category},
        "latency_ms": {
            "ttft_p50": percentile(ttft, 50),
            "ttft_p95": percentile(ttft, 95),
            "total_p50": percentile(total, 50),
            "total_p95": percentile(total, 95),
        },
        "gate": gate.as_dict(),
        "questions": record_dicts(records),
    }
    report = clean_floats(report)
    md_path = write_report(
        args.out, "generation", report, render_markdown(report, gate_lines(gate))
    )

    for m in (*METRICS, *JUDGE_METRICS):
        if m in overall:
            print(f"{m:<22}{fmt(overall[m]):>8}")
    print("\n".join(gate_lines(gate)))
    print(f"report: {md_path}")
    return 0 if gate.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate end-to-end answer generation.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="report directory")
    parser.add_argument("--profile", help="threshold profile to gate against (default: auto)")
    parser.add_argument("--no-gate", action="store_true", help="report only, always exit 0")
    parser.add_argument("--no-judge", action="store_true", help="skip the LLM-as-judge")
    parser.add_argument("--limit", type=int, help="only the first N questions (smoke runs)")
    args = parser.parse_args(argv)
    configure_logging(get_settings().log_level)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())

"""Ragas evaluation harness.

Run with: ``python -m eval.run_eval``

Reads ``golden_dataset.json``, hits the live RAG pipeline for each sample,
collects answers + retrieved contexts, and scores them with Ragas. Exits
non-zero if any metric falls below the configured threshold — that's what
gates the CI workflow.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

DATASET_PATH = Path(__file__).parent / "golden_dataset.json"
RESULTS_DIR = Path("eval_results")
RESULTS_DIR.mkdir(exist_ok=True)

THRESHOLDS: dict[str, float] = {
    "faithfulness": 0.90,
    "answer_relevancy": 0.85,
    "context_precision": 0.80,
    "context_recall": 0.75,
}


async def collect_predictions(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.db.session import async_session_factory
    from app.rag.pipeline import get_pipeline

    pipeline = get_pipeline()
    rows: list[dict[str, Any]] = []
    for s in samples:
        async with async_session_factory() as session:
            answer_buf: list[str] = []
            contexts: list[str] = []
            async for ev in pipeline.stream(
                session, question=s["question"], course_code=s.get("course_code")
            ):
                if ev.type == "context":
                    contexts = [c["snippet"] for c in ev.data]  # type: ignore[index]
                elif ev.type == "token":
                    answer_buf.append(str(ev.data))
            rows.append(
                {
                    "question": s["question"],
                    "answer": "".join(answer_buf),
                    "contexts": contexts or [""],
                    "ground_truth": s["ground_truth"],
                }
            )
    return rows


def evaluate(rows: list[dict[str, Any]]) -> dict[str, float]:
    from datasets import Dataset
    from ragas import evaluate as ragas_evaluate
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    ds = Dataset.from_list(rows)
    result = ragas_evaluate(
        ds,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
    )
    return {k: float(v) for k, v in result.items() if isinstance(v, (int, float))}


def gate(scores: dict[str, float]) -> int:
    failed: list[str] = []
    for metric, threshold in THRESHOLDS.items():
        actual = scores.get(metric)
        if actual is None:
            continue
        marker = "✓" if actual >= threshold else "✗"
        print(f"  {marker} {metric:<20s} {actual:.3f}  (threshold {threshold:.2f})")
        if actual < threshold:
            failed.append(metric)
    return 1 if failed else 0


async def main() -> int:
    samples = json.loads(DATASET_PATH.read_text())["samples"]
    print(f"Running {len(samples)} eval samples through the pipeline...")
    rows = await collect_predictions(samples)

    (RESULTS_DIR / "predictions.json").write_text(json.dumps(rows, indent=2))

    if os.environ.get("EVAL_SKIP_RAGAS") == "1":
        print("EVAL_SKIP_RAGAS=1 — predictions written, skipping Ragas scoring.")
        return 0

    print("Scoring with Ragas...")
    scores = evaluate(rows)
    (RESULTS_DIR / "scores.json").write_text(json.dumps(scores, indent=2))

    print("\nResults:")
    return gate(scores)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

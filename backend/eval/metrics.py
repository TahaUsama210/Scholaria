"""Pure metric functions for the retrieval and generation evals (no I/O).

Ranking metrics use binary relevance over a ranked list of *distinct* items
(sections, after de-duplicating chunks); ``relevant`` must be non-empty.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Hashable, Iterable, Sequence

RANKING_METRICS = ("hit@1", "recall@5", "mrr@10", "ndcg@10")


# --- ranking ----------------------------------------------------------------


def dedupe[T: Hashable](items: Iterable[T]) -> list[T]:
    """Keep the first occurrence of each item, preserving order."""
    seen: set[T] = set()
    out: list[T] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def hit_at_k[T: Hashable](ranked: Sequence[T], relevant: set[T], k: int) -> float:
    return 1.0 if any(item in relevant for item in ranked[:k]) else 0.0


def recall_at_k[T: Hashable](ranked: Sequence[T], relevant: set[T], k: int) -> float:
    return len(relevant.intersection(ranked[:k])) / len(relevant)


def mrr_at_k[T: Hashable](ranked: Sequence[T], relevant: set[T], k: int) -> float:
    """Reciprocal rank of the first relevant item within the top k, else 0."""
    for rank, item in enumerate(ranked[:k], start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k[T: Hashable](ranked: Sequence[T], relevant: set[T], k: int) -> float:
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(ranked[:k], start=1)
        if item in relevant
    )
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    return dcg / ideal


def ranking_scores[T: Hashable](ranked: Sequence[T], relevant: set[T]) -> dict[str, float]:
    if not relevant:
        raise ValueError("ranking metrics need at least one relevant item")
    return {
        "hit@1": hit_at_k(ranked, relevant, 1),
        "recall@5": recall_at_k(ranked, relevant, 5),
        "mrr@10": mrr_at_k(ranked, relevant, 10),
        "ndcg@10": ndcg_at_k(ranked, relevant, 10),
    }


def first_relevant_rank[T: Hashable](ranked: Sequence[T], relevant: set[T]) -> int | None:
    return next((i for i, item in enumerate(ranked, start=1) if item in relevant), None)


# --- aggregation ------------------------------------------------------------


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile, ``q`` in [0, 100]."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def paired_bootstrap_ci(
    a: Sequence[float],
    b: Sequence[float],
    *,
    iterations: int = 5000,
    confidence: float = 0.95,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Mean of ``b - a`` and its percentile bootstrap CI, resampling questions.

    Paired because both systems answer the same questions; deterministic for a seed.
    """
    if len(a) != len(b) or not a:
        raise ValueError("need two equal-length, non-empty samples")
    diffs = [y - x for x, y in zip(a, b, strict=True)]
    rng = random.Random(seed)  # noqa: S311 - resampling, not cryptography
    n = len(diffs)
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(iterations))
    tail = (1 - confidence) / 2 * 100
    return mean(diffs), percentile(means, tail), percentile(means, 100 - tail)


# --- generation ---------------------------------------------------------------

# Same syntax the generator parses: [1], [1, 2], [1; 3].
CITATION_RE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
# Citations placed after the full stop ("... gradients. [1]") belong to that sentence.
_TRAILING_CITES_RE = re.compile(r"([.!?])((?:\s*\[\d+(?:\s*[,;]\s*\d+)*\])+)")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
MIN_CLAIM_WORDS = 4

_REFUSAL_RE = re.compile(
    r"(could ?n[o']?t|can ?n[o']?t|unable to|was ?n[o']?t able to) (find|locate|answer)"
    r"|\b(materials?|context|documents?|notes|sources?)\b[^.]{0,40}"
    r"\b(do(es)? ?n[o']?t|did ?n[o']?t)\b[^.]{0,20}\b(contain|cover|mention|include|address"
    r"|discuss|say|provide)"
    r"|\bnot (covered|mentioned|addressed|discussed|included) in\b"
    r"|\bno (relevant )?information\b"
    r"|\b(insufficient|not enough) (information|context)\b",
    re.IGNORECASE,
)
_REFUSAL_WINDOW = 300  # a refusal is announced up front, not buried in an answer


def cited_markers(answer: str) -> list[int]:
    """Every marker in order of appearance, including out-of-range ones."""
    return [int(n) for group in CITATION_RE.findall(answer) for n in re.split(r"[,;]", group)]


def citations_valid(answer: str, n_context: int) -> bool:
    """True when every [n] refers to one of the ``n_context`` passages (vacuous if none)."""
    return all(1 <= m <= n_context for m in cited_markers(answer))


def claim_sentences(answer: str) -> list[str]:
    """Answer sentences that make a claim: >= MIN_CLAIM_WORDS words, not a lead-in ending ':'."""
    text = _TRAILING_CITES_RE.sub(lambda m: f"{m.group(2)}{m.group(1)}", answer)
    out: list[str] = []
    for line in text.splitlines():
        line = _BULLET_RE.sub("", line).strip()
        for sentence in _SENTENCE_END_RE.split(line):
            sentence = sentence.strip()
            words = CITATION_RE.sub("", sentence).split()
            if len(words) >= MIN_CLAIM_WORDS and not sentence.rstrip("*").endswith(":"):
                out.append(sentence)
    return out


def citation_coverage(answer: str) -> float | None:
    """Share of claim sentences carrying at least one [n]; None if there are none."""
    sentences = claim_sentences(answer)
    if not sentences:
        return None
    return sum(1 for s in sentences if CITATION_RE.search(s)) / len(sentences)


def is_refusal(answer: str) -> bool:
    """Heuristic: the answer says up front that the materials don't cover the question."""
    return bool(_REFUSAL_RE.search(answer[:_REFUSAL_WINDOW]))

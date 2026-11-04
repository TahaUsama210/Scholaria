"""Golden dataset loading and validation against the demo corpus.

Run ``python -m eval.dataset`` to validate labels and print dataset statistics.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.demo_corpus import Course, load_corpus

DATASET_PATH = Path(__file__).parent / "golden_dataset.json"
SUPPORTED_VERSION = 2

CATEGORIES = ("definition", "keyword", "paraphrase", "multi_hop", "out_of_scope")
# Out-of-scope questions have no relevant section, so they are excluded from
# ranking metrics and scored on refusal instead.
RANKED_CATEGORIES = tuple(c for c in CATEGORIES if c != "out_of_scope")

MAX_QUESTION_CHARS = 2000  # ChatRequest.question limit

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORD_TEXT = (
    "a an and are as at be been but by can could do does for from has have how i if in "
    "into is it its make makes might more much my of on or so some than that the their "
    "them then there these they this to too was what when where whether which while who "
    "why will with would you your"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


class DatasetError(ValueError):
    """Raised when the golden dataset does not match its schema."""


@dataclass(frozen=True, slots=True, order=True)
class SectionRef:
    course_code: str
    page: int

    def __str__(self) -> str:
        return f"{self.course_code}:p{self.page}"


@dataclass(frozen=True, slots=True)
class GoldenQuestion:
    id: str
    category: str
    question: str
    relevant: tuple[SectionRef, ...]
    reference_answer: str
    keyword: str | None = None

    @property
    def in_scope(self) -> bool:
        return self.category != "out_of_scope"


@dataclass(frozen=True, slots=True)
class GoldenDataset:
    version: int
    description: str
    questions: tuple[GoldenQuestion, ...]

    def by_category(self) -> dict[str, list[GoldenQuestion]]:
        out: dict[str, list[GoldenQuestion]] = {c: [] for c in CATEGORIES}
        for q in self.questions:
            out[q.category].append(q)
        return out


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise DatasetError(msg)


def _parse_question(raw: Any, index: int) -> GoldenQuestion:
    where = f"questions[{index}]"
    _require(isinstance(raw, dict), f"{where}: expected an object")
    for key in ("id", "category", "question", "reference_answer"):
        _require(isinstance(raw.get(key), str) and raw[key].strip(), f"{where}: '{key}' required")
    where = f"{where} ({raw['id']})"
    category = raw["category"]
    _require(category in CATEGORIES, f"{where}: unknown category {category!r}")
    _require(len(raw["question"]) <= MAX_QUESTION_CHARS, f"{where}: question too long")

    labels = raw.get("relevant")
    _require(isinstance(labels, list), f"{where}: 'relevant' must be a list")
    relevant: list[SectionRef] = []
    for label in labels:
        _require(
            isinstance(label, dict)
            and isinstance(label.get("course_code"), str)
            and isinstance(label.get("page"), int)
            and not isinstance(label.get("page"), bool),
            f"{where}: labels must be {{course_code: str, page: int}}",
        )
        relevant.append(SectionRef(label["course_code"], label["page"]))
    _require(len(set(relevant)) == len(relevant), f"{where}: duplicate labels")

    if category == "out_of_scope":
        _require(not relevant, f"{where}: out_of_scope questions take no labels")
    else:
        _require(bool(relevant), f"{where}: at least one relevant section required")
    if category == "multi_hop":
        _require(len(relevant) >= 2, f"{where}: multi_hop needs two or more sections")

    keyword = raw.get("keyword")
    if category == "keyword":
        _require(isinstance(keyword, str) and bool(keyword.strip()), f"{where}: 'keyword' required")
        _require(keyword in raw["question"], f"{where}: keyword must appear in the question")
    else:
        _require(keyword is None, f"{where}: 'keyword' only applies to keyword questions")

    return GoldenQuestion(
        id=raw["id"],
        category=category,
        question=raw["question"],
        relevant=tuple(relevant),
        reference_answer=raw["reference_answer"],
        keyword=keyword,
    )


def parse_dataset(data: Any) -> GoldenDataset:
    _require(isinstance(data, dict), "dataset must be a JSON object")
    _require(
        data.get("version") == SUPPORTED_VERSION,
        f"unsupported dataset version {data.get('version')!r} (expected {SUPPORTED_VERSION})",
    )
    raw_questions = data.get("questions")
    _require(
        isinstance(raw_questions, list) and bool(raw_questions), "'questions' must be non-empty"
    )
    questions = tuple(_parse_question(q, i) for i, q in enumerate(raw_questions))
    ids = Counter(q.id for q in questions)
    dupes = sorted(i for i, n in ids.items() if n > 1)
    _require(not dupes, f"duplicate question ids: {dupes}")
    return GoldenDataset(
        version=data["version"], description=str(data.get("description", "")), questions=questions
    )


def load_dataset(path: Path = DATASET_PATH) -> GoldenDataset:
    return parse_dataset(json.loads(path.read_text(encoding="utf-8")))


def content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def lexical_overlap(question: str, passage: str) -> float:
    """Share of the question's content words that also occur in ``passage``."""
    q = content_words(question)
    return len(q & content_words(passage)) / len(q) if q else 0.0


def validate_against_corpus(dataset: GoldenDataset, corpus: list[Course]) -> list[str]:
    """Problems that make a label meaningless; an empty list means the dataset is valid."""
    courses = {c.course_code: c for c in corpus}
    problems: list[str] = []
    for q in dataset.questions:
        texts: list[str] = []
        for ref in q.relevant:
            course = courses.get(ref.course_code)
            if course is None:
                problems.append(f"{q.id}: unknown course {ref.course_code}")
            elif not 1 <= ref.page <= len(course.sections):
                problems.append(f"{q.id}: {ref} out of range (1..{len(course.sections)})")
            else:
                section = course.section(ref.page)
                texts.append(f"{section.title}\n{section.text}")
        if q.keyword and texts and not any(q.keyword in t for t in texts):
            problems.append(f"{q.id}: keyword {q.keyword!r} not found in labelled sections")
    return problems


def overlap_by_category(dataset: GoldenDataset, corpus: list[Course]) -> dict[str, float]:
    """Mean lexical overlap between each question and its best-matching labelled section."""
    courses = {c.course_code: c for c in corpus}
    out: dict[str, float] = {}
    for category, questions in dataset.by_category().items():
        scores = [
            max(
                lexical_overlap(q.question, courses[r.course_code].section(r.page).text)
                for r in q.relevant
            )
            for q in questions
            if q.relevant
        ]
        if scores:
            out[category] = sum(scores) / len(scores)
    return out


def main() -> int:
    dataset = load_dataset()
    corpus = load_corpus()
    problems = validate_against_corpus(dataset, corpus)
    counts = Counter(q.category for q in dataset.questions)
    sections = sum(len(c.sections) for c in corpus)
    print(f"golden dataset v{dataset.version}: {len(dataset.questions)} questions")
    print(f"corpus: {len(corpus)} courses, {sections} sections")
    overlap = overlap_by_category(dataset, corpus)
    for category in CATEGORIES:
        extra = f"  mean lexical overlap {overlap[category]:.2f}" if category in overlap else ""
        print(f"  {category:<13} {counts[category]:>3}{extra}")
    labelled = {r for q in dataset.questions for r in q.relevant}
    print(f"sections with at least one question: {len(labelled)}/{sections}")
    for p in problems:
        print(f"ERROR {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

"""Eval harness unit tests: metric maths, dataset/corpus validation, gating, judge parsing.

No database or network: everything here runs on the offline unit-test profile.
"""

import json
import math
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pypdf import PdfReader

from app.rag.pipeline import NO_CONTEXT_MESSAGE
from app.services.ingestion import extract_pages
from eval import metrics as m
from eval.common import THRESHOLDS_PATH, Check, ThresholdProfile, load_profiles, run_gate
from eval.common import select_profile as pick_profile
from eval.dataset import (
    CATEGORIES,
    DatasetError,
    SectionRef,
    load_dataset,
    overlap_by_category,
    parse_dataset,
    validate_against_corpus,
)
from eval.judge import Judge, JudgeParseError, judge_settings_status, parse_judgement
from scripts.demo_corpus import PDF_ENCODING, CorpusError, load_corpus, parse_course, render_pdf
from scripts.seed_demo_docs import main as seed_main

# --- ranking metrics --------------------------------------------------------------

RANKED = ["a", "b", "c", "d"]


def test_ranking_metrics_single_relevant() -> None:
    scores = m.ranking_scores(RANKED, {"c"})
    assert scores == {
        "hit@1": 0.0,
        "recall@5": 1.0,
        "mrr@10": pytest.approx(1 / 3),
        "ndcg@10": pytest.approx(1 / math.log2(4)),
    }


def test_ranking_metrics_two_relevant() -> None:
    scores = m.ranking_scores(RANKED, {"a", "d"})
    assert scores["hit@1"] == 1.0
    assert scores["mrr@10"] == 1.0
    ideal = 1 + 1 / math.log2(3)
    assert scores["ndcg@10"] == pytest.approx((1 + 1 / math.log2(5)) / ideal)


def test_ranking_metrics_respect_cutoffs() -> None:
    ranked = [str(i) for i in range(12)]
    assert m.recall_at_k(ranked, {"0", "7"}, 5) == 0.5
    assert m.mrr_at_k(ranked, {"11"}, 10) == 0.0
    assert m.ndcg_at_k(ranked, {"11"}, 10) == 0.0
    assert m.ranking_scores(ranked, {"missing"}) == dict.fromkeys(m.RANKING_METRICS, 0.0)


def test_ranking_scores_needs_relevant_items() -> None:
    with pytest.raises(ValueError):
        m.ranking_scores(RANKED, set())


def test_dedupe_and_first_relevant_rank() -> None:
    assert m.dedupe(["x", "y", "x", "z", "y"]) == ["x", "y", "z"]
    assert m.first_relevant_rank(RANKED, {"c", "d"}) == 3
    assert m.first_relevant_rank(RANKED, {"q"}) is None


def test_percentile_and_mean() -> None:
    assert m.percentile([4.0, 1.0, 3.0, 2.0], 50) == 2.5
    assert m.percentile([1.0, 2.0], 100) == 2.0
    assert math.isnan(m.mean([]))


def test_paired_bootstrap_ci() -> None:
    same = [0.0, 1.0, 0.5, 1.0]
    assert m.paired_bootstrap_ci(same, same) == (0.0, 0.0, 0.0)

    a = [0.0, 0.5, 0.0, 1.0, 0.0, 0.5] * 5
    b = [1.0, 1.0, 0.5, 1.0, 0.5, 1.0] * 5
    delta, lo, hi = m.paired_bootstrap_ci(a, b, seed=1)
    assert delta == pytest.approx(0.5)
    assert 0 < lo <= delta <= hi
    assert m.paired_bootstrap_ci(a, b, seed=1) == (delta, lo, hi)  # deterministic
    with pytest.raises(ValueError):
        m.paired_bootstrap_ci([1.0], [1.0, 2.0])


# --- generation metrics ------------------------------------------------------------


def test_cited_markers_and_validity() -> None:
    answer = "Gradients flow backward [1, 2]. Activations are reused [3]. Memory [2;4]."
    assert m.cited_markers(answer) == [1, 2, 3, 2, 4]
    assert m.citations_valid(answer, 4)
    assert not m.citations_valid(answer, 3)
    assert not m.citations_valid("Zero is never a valid marker [0].", 5)
    assert m.citations_valid("No citations at all.", 0)


def test_claim_sentences_handle_trailing_citations_bullets_and_lead_ins() -> None:
    answer = (
        "Backpropagation computes every gradient. [1]\n\n"
        "Key points:\n"
        "- The cost is about one forward pass [2].\n"
        "1. Memory for activations is the price.\n"
        "Short one [1]."
    )
    assert m.claim_sentences(answer) == [
        "Backpropagation computes every gradient [1].",
        "The cost is about one forward pass [2].",
        "Memory for activations is the price.",
    ]
    assert m.citation_coverage(answer) == pytest.approx(2 / 3)
    assert m.citation_coverage("Yes [1].") is None


@pytest.mark.parametrize(
    "answer",
    [
        NO_CONTEXT_MESSAGE,
        "I could not find this in the course materials.",
        "The provided context does not cover cricket.",
        "The notes don't mention Rust's borrow checker.",
        "There is not enough information in the context to answer.",
    ],
)
def test_refusals_are_detected(answer: str) -> None:
    assert m.is_refusal(answer)


@pytest.mark.parametrize(
    "answer",
    [
        "The inode does not contain the file name [1].",
        "A hash index cannot serve range scans [2].",
        "Backpropagation reuses activations [1]. " * 10 + "We could not find a closed form.",
    ],
)
def test_answers_are_not_refusals(answer: str) -> None:
    assert not m.is_refusal(answer)


# --- corpus ---------------------------------------------------------------------------

CORPUS = load_corpus()
COURSES = {c.course_code: c for c in CORPUS}


def test_corpus_shape() -> None:
    assert sorted(COURSES) == ["CS348", "CS350", "CS456", "CS480"]
    for course in CORPUS:
        assert 7 <= len(course.sections) <= 8, course.course_code
        for section in course.sections:
            assert 250 <= section.word_count <= 400, (course.course_code, section.title)
            section.text.encode(PDF_ENCODING)  # standard PDF fonts are WinAnsi only


@pytest.mark.parametrize(
    ("course", "page", "phrase"),
    [
        # Facts carried over from the original six demo sections.
        ("CS480", 1, "chain rule"),
        ("CS480", 1, "on the same order as one forward pass"),
        ("CS480", 2, "validation loss"),
        ("CS480", 3, "irreducible noise"),
        ("CS348", 1, "non-transitively dependent on every candidate key"),
        ("CS348", 3, "Durability: once committed"),
        ("CS348", 4, "O(log n)"),
        ("CS348", 4, "HNSW"),
        # Exact identifiers that keyword questions depend on.
        ("CS348", 2, "BCNF"),
        ("CS350", 2, "SIGKILL"),
        ("CS456", 3, "TCP Reno"),
        ("CS480", 4, "ReLU"),
        ("CS350", 1, "fork()"),
        ("CS348", 7, "WAL"),
        ("CS350", 7, "LRU"),
    ],
)
def test_corpus_contains(course: str, page: int, phrase: str) -> None:
    assert phrase in COURSES[course].section(page).text


def test_parse_course_rejects_malformed_input() -> None:
    with pytest.raises(CorpusError, match="front matter"):
        parse_course("## Heading\n\nBody text.")
    with pytest.raises(CorpusError, match="empty"):
        parse_course("---\ncourse_code: X1\ncourse_name: X\n---\n\n## Empty\n\n")


def test_pdf_is_deterministic_one_section_per_page() -> None:
    course = COURSES["CS350"]
    pdf = render_pdf(course)
    assert pdf == render_pdf(course)
    assert len(PdfReader(BytesIO(pdf)).pages) == len(course.sections)

    pages = extract_pages(pdf)  # the real ingestion extractor
    assert [p for p, _ in pages] == [s.page for s in course.sections]
    for (page, text), section in zip(pages, course.sections, strict=True):
        assert f"{page}. {section.title}" in text
        assert f"Page {page} of {len(course.sections)}" in text
        assert section.paragraphs[-1].split()[-1] in text


def test_seed_pdf_only_writes_files(tmp_path: Path) -> None:
    assert seed_main(["--pdf-only", "--out-dir", str(tmp_path), "--course", "cs480"]) == 0
    assert [p.name for p in tmp_path.iterdir()] == [COURSES["CS480"].filename]
    assert (tmp_path / COURSES["CS480"].filename).read_bytes() == render_pdf(COURSES["CS480"])


# --- golden dataset -------------------------------------------------------------------

DATASET = load_dataset()


def test_golden_dataset_is_valid_against_corpus() -> None:
    assert DATASET.version == 2
    assert validate_against_corpus(DATASET, CORPUS) == []
    by_cat = DATASET.by_category()
    assert set(by_cat) == set(CATEGORIES)
    assert 45 <= len(DATASET.questions) <= 60
    assert all(len(qs) >= 5 for qs in by_cat.values())
    assert all(len(q.relevant) >= 2 for q in by_cat["multi_hop"])
    assert all(not q.relevant for q in by_cat["out_of_scope"])


def test_paraphrase_questions_have_low_lexical_overlap() -> None:
    overlap = overlap_by_category(DATASET, CORPUS)
    assert overlap["paraphrase"] < 0.5
    assert overlap["paraphrase"] < overlap["definition"] < overlap["keyword"] + 0.1


def _raw_question(**overrides: Any) -> dict[str, Any]:
    q: dict[str, Any] = {
        "id": "q1",
        "category": "definition",
        "question": "What is WAL?",
        "relevant": [{"course_code": "CS348", "page": 7}],
        "reference_answer": "Write-ahead logging.",
    }
    q.update(overrides)
    return q


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"category": "trivia"}, "unknown category"),
        ({"category": "out_of_scope"}, "take no labels"),
        ({"category": "multi_hop"}, "two or more"),
        ({"relevant": []}, "at least one"),
        ({"category": "keyword", "keyword": "ARIES"}, "must appear in the question"),
        ({"keyword": "WAL"}, "only applies"),
        ({"relevant": [{"course_code": "CS348", "page": "7"}]}, "labels must be"),
        ({"question": "x" * 2001}, "too long"),
    ],
)
def test_parse_dataset_rejects_bad_questions(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(DatasetError, match=message):
        parse_dataset({"version": 2, "questions": [_raw_question(**overrides)]})


def test_parse_dataset_rejects_bad_files() -> None:
    with pytest.raises(DatasetError, match="version"):
        parse_dataset({"version": 1, "questions": [_raw_question()]})
    with pytest.raises(DatasetError, match="duplicate"):
        parse_dataset({"version": 2, "questions": [_raw_question(), _raw_question()]})


def test_validate_against_corpus_flags_bad_labels() -> None:
    q = DATASET.questions[0]
    bad = replace(
        DATASET,
        questions=(
            replace(q, id="bad-page", relevant=(SectionRef("CS480", 99),)),
            replace(q, id="bad-course", relevant=(SectionRef("CS999", 1),)),
            replace(q, id="bad-kw", category="keyword", keyword="SIGKILL"),
        ),
    )
    problems = validate_against_corpus(bad, CORPUS)
    assert len(problems) == 3
    assert "out of range" in problems[0]
    assert "unknown course" in problems[1]
    assert "not found" in problems[2]


# --- gating ---------------------------------------------------------------------------


def test_threshold_profiles_select_by_settings() -> None:
    profiles = [
        ThresholdProfile("a", {"embedding_provider": "hash", "embedding_dim": "256"}, {}),
        ThresholdProfile("b", {"embedding_provider": "local"}, {}),
    ]
    hash_settings = SimpleNamespace(embedding_provider="hash", embedding_dim=256)
    assert pick_profile(profiles, hash_settings).name == "a"  # type: ignore[union-attr]
    assert pick_profile(profiles, SimpleNamespace(embedding_provider="openai")) is None
    assert pick_profile(profiles, hash_settings, name="b").name == "b"  # type: ignore[union-attr]
    with pytest.raises(KeyError):
        pick_profile(profiles, hash_settings, name="nope")


def test_run_gate_min_max_and_missing_metrics() -> None:
    profile = ThresholdProfile("p", {}, {"hit@1": 0.8, "mrr@10": 0.5}, {"error_rate": 0.0})
    passing = run_gate({"hit@1": 0.8, "mrr@10": 0.9, "error_rate": 0.0}, profile)
    assert passing.gated and passing.passed
    failing = run_gate({"hit@1": 0.79, "error_rate": 0.02}, profile)
    assert not failing.passed
    assert [c.metric for c in failing.checks if not c.passed] == ["hit@1", "mrr@10", "error_rate"]
    assert not Check("x", float("nan"), 0.0, "min").passed
    ungated = run_gate({}, None)
    assert not ungated.gated and ungated.passed


def test_thresholds_file_names_known_metrics() -> None:
    from eval.generation_eval import METRICS

    raw = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    assert {"retrieval", "generation"} <= set(raw)
    for section, known in (("retrieval", set(m.RANKING_METRICS)), ("generation", set(METRICS))):
        profiles = load_profiles(section)
        assert profiles, section
        for p in profiles:
            assert set(p.minimum) | set(p.maximum) <= known, p.name
            assert all(0.0 <= v <= 1.0 for v in (*p.minimum.values(), *p.maximum.values()))


# --- LLM judge --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ('{"score": 4, "reason": "minor gap"}', (4, "minor gap")),
        ('Sure!\n```json\n{"score": "5", "reason": "ok"}\n```', (5, "ok")),
    ],
)
def test_parse_judgement(reply: str, expected: tuple[int, str]) -> None:
    assert parse_judgement(reply) == expected


@pytest.mark.parametrize("reply", ["no json here", '{"score": 9}', '{"score": true}', "{bad"])
def test_parse_judgement_rejects(reply: str) -> None:
    with pytest.raises(JudgeParseError):
        parse_judgement(reply)


class _StubCompletions:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        message = SimpleNamespace(content=self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


async def test_judge_normalises_scores_and_survives_errors() -> None:
    completions = _StubCompletions('{"score": 5, "reason": "supported"}')
    judge = Judge(SimpleNamespace(chat=SimpleNamespace(completions=completions)), model="m")
    score = await judge.faithfulness(question="q", answer="a [1]", passages=["p1", "p2"])
    assert (score.score, score.raw, score.error) == (1.0, 5, None)
    prompt = completions.calls[0]["messages"][1]["content"]
    assert "[1] p1" in prompt and "[2] p2" in prompt
    assert completions.calls[0]["temperature"] == 0

    broken = _StubCompletions(RuntimeError("rate limited"))
    judge = Judge(SimpleNamespace(chat=SimpleNamespace(completions=broken)), model="m")
    failed = await judge.correctness(question="q", answer="a", reference="r")
    assert failed.score is None
    assert failed.error is not None and "rate limited" in failed.error


def test_judge_enabled_only_with_a_real_llm() -> None:
    fake = SimpleNamespace(llm_provider="fake", resolved_llm_api_key=None, llm_base_url=None)
    assert judge_settings_status(fake)[0] is False
    keyless = SimpleNamespace(llm_provider="openai", resolved_llm_api_key=None, llm_base_url=None)
    assert judge_settings_status(keyless)[0] is False
    ollama = SimpleNamespace(
        llm_provider="openai", resolved_llm_api_key=None, llm_base_url="http://localhost:11434/v1"
    )
    assert judge_settings_status(ollama) == (True, "enabled")

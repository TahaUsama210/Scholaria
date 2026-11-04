"""RagPipeline without a database: SQL retrieval is stubbed, everything else is real."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace
from typing import Any

import pytest
from prometheus_client import REGISTRY

from app.core.config import get_settings
from app.rag.embeddings import HashEmbedder
from app.rag.generator import FakeGenerator, HistoryTurn, StreamEvent
from app.rag.pipeline import NO_CONTEXT_MESSAGE, RagPipeline
from app.rag.reranker import NoopReranker
from app.rag.retriever import RetrievedChunk
from tests.helpers import chunk


@dataclass
class Turn:
    role: str
    content: str


def _count(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _make(
    dense: list[RetrievedChunk], sparse: list[RetrievedChunk], generator: Any = None
) -> tuple[RagPipeline, list[str]]:
    pipeline = RagPipeline(
        settings=get_settings(),
        embedder=HashEmbedder(dim=256),
        reranker=NoopReranker(),
        generator=generator or FakeGenerator(),
    )
    queries: list[str] = []

    async def fake_dense_and_sparse(
        session: object, *, query: str, query_vec: list[float], course_code: str | None
    ) -> tuple[list[RetrievedChunk], list[RetrievedChunk]]:
        assert len(query_vec) == 256
        queries.append(query)
        return dense, sparse

    pipeline.retriever._dense_and_sparse = fake_dense_and_sparse  # type: ignore[method-assign]
    return pipeline, queries


async def _run(pipeline: RagPipeline, **kwargs: Any) -> list[StreamEvent]:
    return [ev async for ev in pipeline.stream(None, **kwargs)]  # type: ignore[arg-type]


async def test_event_order_and_outcome_metric() -> None:
    a = chunk("Overfitting occurs when a model memorises noise in training data.")
    b = chunk("Regularisation such as dropout reduces overfitting.")
    pipeline, _ = _make([a, b], [b])
    answered = _count("scholaria_rag_requests_total", {"outcome": "answered"})
    ttft = _count("scholaria_rag_stage_duration_seconds_count", {"stage": "generate_ttft"})

    events = await _run(pipeline, question="  What is overfitting?  ")
    types = [e.type for e in events]
    assert types[0] == "context" and types[-2:] == ["citations", "done"]
    assert set(types[1:-2]) == {"token"}

    context = events[0].data
    assert isinstance(context, list)
    assert [c["marker"] for c in context] == [1, 2]
    assert context[0]["chunk_id"] == str(b.chunk_id)  # in both lists, so RRF ranks it first
    done = events[-1].data
    assert isinstance(done, dict)
    assert done["outcome"] == "answered" and "standalone_question" not in done
    assert done["answer"] == "".join(str(e.data) for e in events if e.type == "token")
    assert _count("scholaria_rag_requests_total", {"outcome": "answered"}) == answered + 1
    assert (
        _count("scholaria_rag_stage_duration_seconds_count", {"stage": "generate_ttft"}) == ttft + 1
    )


async def test_no_context_path() -> None:
    pipeline, _ = _make([], [])
    before = _count("scholaria_rag_requests_total", {"outcome": "no_context"})
    events = await _run(pipeline, question="Anything?")
    assert [(e.type, e.data) for e in events] == [
        ("context", []),
        ("token", NO_CONTEXT_MESSAGE),
        ("citations", []),
        ("done", {"answer": NO_CONTEXT_MESSAGE, "outcome": "no_context"}),
    ]
    assert _count("scholaria_rag_requests_total", {"outcome": "no_context"}) == before + 1


async def test_history_is_condensed_before_retrieval() -> None:
    pipeline, queries = _make([chunk("Overfitting is memorising noise.")], [])
    condensed = _count("scholaria_rag_stage_duration_seconds_count", {"stage": "condense"})
    history = [Turn("user", "What is overfitting?"), Turn("assistant", "Memorising noise [1].")]

    events = await _run(pipeline, question="Why is it bad?", history=history)
    standalone = "Why is it bad? (follow-up to: What is overfitting?)"
    assert queries == [standalone]
    assert events[-1].data["standalone_question"] == standalone  # type: ignore[index]
    assert (
        _count("scholaria_rag_stage_duration_seconds_count", {"stage": "condense"}) == condensed + 1
    )


async def test_empty_history_skips_condense() -> None:
    pipeline, queries = _make([chunk("x")], [])
    await _run(pipeline, question="Plain question", history=[])
    assert queries == ["Plain question"]


class _BrokenCondense(FakeGenerator):
    async def condense(self, *, question: str, history: Sequence[HistoryTurn]) -> str:
        raise TimeoutError("llm down")


async def test_condense_failure_falls_back_to_the_raw_question() -> None:
    pipeline, queries = _make([chunk("x")], [], generator=_BrokenCondense())
    events = await _run(pipeline, question="Why?", history=[Turn("user", "Earlier")])
    assert queries == ["Why?"]
    assert events[-1].type == "done"


class _BrokenGenerator(FakeGenerator):
    async def _stream_text(
        self, *, question: str, chunks: list[RetrievedChunk]
    ) -> AsyncIterator[str]:
        yield "partial "
        raise RuntimeError("provider exploded")


async def test_generation_error_propagates_and_is_counted() -> None:
    pipeline, _ = _make([chunk("x")], [], generator=_BrokenGenerator())
    before = _count("scholaria_rag_requests_total", {"outcome": "error"})
    seen: list[str] = []
    with pytest.raises(RuntimeError, match="provider exploded"):
        async for ev in pipeline.stream(None, question="q"):  # type: ignore[arg-type]
            seen.append(ev.type)
    assert seen == ["context", "token"]
    assert _count("scholaria_rag_requests_total", {"outcome": "error"}) == before + 1


async def test_consumer_closing_early_is_not_an_error() -> None:
    pipeline, _ = _make([chunk("Some long content. " * 20)], [])
    before = _count("scholaria_rag_requests_total", {"outcome": "error"})
    stream = pipeline.stream(None, question="content")  # type: ignore[arg-type]
    assert (await anext(stream)).type == "context"
    assert (await anext(stream)).type == "token"
    await stream.aclose()  # client disconnected mid-answer
    assert _count("scholaria_rag_requests_total", {"outcome": "error"}) == before


class _FixedScoreReranker(NoopReranker):
    def __init__(self, score: float) -> None:
        self._score = score

    async def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_n: int
    ) -> list[RetrievedChunk]:
        return [replace(c, score=self._score) for c in chunks[:top_n]]


def _with_threshold(score: float, min_score: float | None, provider: str) -> RagPipeline:
    pipeline, _ = _make([chunk("Paging maps virtual pages to frames.")], [])
    pipeline._settings = get_settings().model_copy(
        update={"rerank_min_score": min_score, "reranker_provider": provider}
    )
    pipeline._reranker = _FixedScoreReranker(score)
    return pipeline


@pytest.mark.parametrize(
    ("score", "min_score", "provider", "outcome"),
    [
        (-10.0, -9.0, "local", "no_context"),  # weak best match -> refuse
        (-8.0, -9.0, "local", "answered"),
        (-10.0, None, "local", "answered"),  # threshold disabled
        # No reranker: scores are RRF values, so a reranker floor must not apply.
        (0.016, 0.3, "none", "answered"),
    ],
)
async def test_rerank_min_score_gates_answering(
    score: float, min_score: float | None, provider: str, outcome: str
) -> None:
    pipeline = _with_threshold(score, min_score, provider)
    events = await _run(pipeline, question="How does paging work?")
    done = events[-1].data
    assert isinstance(done, dict) and done["outcome"] == outcome
    if outcome == "no_context":
        assert events[0].data == [] and done["answer"] == NO_CONTEXT_MESSAGE

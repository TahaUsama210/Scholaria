import sys
import threading
import types
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from app.core.config import Settings
from app.rag.reranker import (
    CohereReranker,
    LocalCrossEncoderReranker,
    NoopReranker,
    make_reranker,
)
from tests.helpers import chunk


class _FakeCrossEncoder:
    predict_threads: ClassVar[list[str]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    def predict(self, sentences: list[tuple[str, str]]) -> list[float]:
        self.predict_threads.append(threading.current_thread().name)
        return [float(len(passage)) for _, passage in sentences]


class _FakeCohereClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def rerank(self, **kwargs: Any) -> SimpleNamespace:
        docs = kwargs["documents"]
        order = sorted(range(len(docs)), key=lambda i: len(docs[i]))[: kwargs["top_n"]]
        return SimpleNamespace(
            results=[
                SimpleNamespace(index=i, relevance_score=1.0 / (r + 1)) for r, i in enumerate(order)
            ]
        )


@pytest.fixture
def fake_models(monkeypatch: pytest.MonkeyPatch) -> None:
    st = types.ModuleType("sentence_transformers")
    st.CrossEncoder = _FakeCrossEncoder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)
    cohere = types.ModuleType("cohere")
    cohere.AsyncClient = _FakeCohereClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cohere", cohere)


async def test_noop_reranker_truncates() -> None:
    chunks = [chunk(str(i)) for i in range(5)]
    assert await NoopReranker().rerank("q", chunks, top_n=2) == chunks[:2]


async def test_cross_encoder_sorts_by_score_off_the_event_loop(fake_models: None) -> None:
    chunks = [chunk("a"), chunk("ccc"), chunk("bb")]
    out = await LocalCrossEncoderReranker("fake").rerank("q", chunks, top_n=2)
    assert [c.content for c in out] == ["ccc", "bb"]
    assert [c.score for c in out] == [3.0, 2.0]
    assert _FakeCrossEncoder.predict_threads[-1] != threading.main_thread().name
    assert await LocalCrossEncoderReranker("fake").rerank("q", [], top_n=2) == []


async def test_cohere_reranker_maps_results(fake_models: None) -> None:
    chunks = [chunk("long passage"), chunk("mid one"), chunk("s")]
    out = await CohereReranker(api_key="k").rerank("q", chunks, top_n=2)
    assert [c.content for c in out] == ["s", "mid one"]
    assert out[0].chunk_id == chunks[2].chunk_id
    assert await CohereReranker(api_key="k").rerank("q", [], top_n=2) == []


def test_make_reranker(fake_models: None) -> None:
    assert isinstance(make_reranker(Settings(reranker_provider="none")), NoopReranker)
    assert isinstance(make_reranker(Settings(reranker_provider="local")), LocalCrossEncoderReranker)
    assert isinstance(
        make_reranker(Settings(reranker_provider="cohere", cohere_api_key="k")), CohereReranker
    )
    with pytest.raises(RuntimeError, match="COHERE_API_KEY"):
        make_reranker(Settings(reranker_provider="cohere", cohere_api_key=None))

import json
import math
import sys
import threading
import types
from collections.abc import Callable, Iterator
from typing import ClassVar

import httpx
import openai
import pytest
from tenacity import wait_none

from app.core.config import Settings
from app.rag.embeddings import (
    EmbeddingDimensionError,
    HashEmbedder,
    LocalEmbedder,
    OpenAIEmbedder,
    hash_tokens,
    is_transient_error,
    make_embedder,
)

_REQ = httpx.Request("POST", "http://llm.test/v1/embeddings")


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# --- hash ---------------------------------------------------------------------


async def test_hash_embedder_is_deterministic_and_l2_normalised() -> None:
    text = "Backpropagation computes gradients with the chain rule"
    a, b = await HashEmbedder(dim=256).embed([text, text])
    assert a == b
    assert len(a) == 256
    assert math.isclose(math.sqrt(sum(v * v for v in a)), 1.0, rel_tol=1e-9)
    # A fresh instance (or process) gives the same vector: no salted hash().
    assert await HashEmbedder(dim=256).embed_one(text) == a


async def test_hash_embedder_similarity_tracks_term_overlap() -> None:
    e = HashEmbedder(dim=256)
    q = await e.embed_one("what is overfitting")
    related = await e.embed_one("Overfitting happens when a model memorises noise")
    unrelated = await e.embed_one("Third normal form removes transitive dependencies")
    assert _cos(q, related) > _cos(q, unrelated)


async def test_hash_embedder_handles_empty_and_stopword_only_text() -> None:
    for text in ("", "the of and"):
        v = await HashEmbedder(dim=64).embed_one(text)
        assert math.isclose(sum(x * x for x in v), 1.0)


async def test_hash_embedder_empty_batch() -> None:
    assert await HashEmbedder(dim=8).embed([]) == []


def test_hash_tokens_fold_plurals_and_drop_stopwords() -> None:
    assert hash_tokens("The gradients and weights of a class") == ["gradient", "weight", "class"]


def test_hash_embedder_rejects_non_positive_dim() -> None:
    with pytest.raises(ValueError):
        HashEmbedder(dim=0)


# --- retry predicate ------------------------------------------------------------


def _status_error(cls: type[openai.APIStatusError], code: int) -> openai.APIStatusError:
    return cls("err", response=httpx.Response(code, request=_REQ), body=None)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (openai.APIConnectionError(request=_REQ), True),
        (openai.APITimeoutError(request=_REQ), True),
        (_status_error(openai.RateLimitError, 429), True),
        (_status_error(openai.InternalServerError, 500), True),
        (_status_error(openai.InternalServerError, 503), True),
        (_status_error(openai.APIStatusError, 408), True),
        (_status_error(openai.AuthenticationError, 401), False),
        (_status_error(openai.PermissionDeniedError, 403), False),
        (_status_error(openai.BadRequestError, 400), False),
        (_status_error(openai.NotFoundError, 404), False),
        (ValueError("bug"), False),
    ],
)
def test_is_transient_error(exc: BaseException, expected: bool) -> None:
    assert is_transient_error(exc) is expected


# --- openai (mock transport) -----------------------------------------------------

Handler = Callable[[httpx.Request], httpx.Response]


def _openai_client(handler: Handler) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        api_key="sk-test",
        base_url="http://llm.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _embedding_response(inputs: list[str], dim: int, *, reverse: bool = False) -> httpx.Response:
    data = [
        {"object": "embedding", "index": i, "embedding": [float(len(t))] + [0.0] * (dim - 1)}
        for i, t in enumerate(inputs)
    ]
    if reverse:
        data.reverse()  # the API doesn't promise order; the embedder sorts by index
    body = {
        "object": "list",
        "data": data,
        "model": "m",
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }
    return httpx.Response(200, json=body)


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(OpenAIEmbedder._embed_batch.retry, "wait", wait_none())  # type: ignore[attr-defined]
    yield


async def test_openai_embedder_batches_and_preserves_order() -> None:
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        calls.append(inputs)
        return _embedding_response(inputs, 4, reverse=True)

    e = OpenAIEmbedder(model="m", dim=4, api_key=None, batch_size=2, client=_openai_client(handler))
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    out = await e.embed(texts)
    assert [len(b) for b in calls] == [2, 2, 1]
    assert [v[0] for v in out] == [1.0, 2.0, 3.0, 4.0, 5.0]


async def test_openai_embedder_retries_transient_errors(no_backoff: None) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503 if attempts == 1 else 429, json={"error": {"message": "x"}})
        return _embedding_response(json.loads(request.content)["input"], 4)

    e = OpenAIEmbedder(model="m", dim=4, api_key="k", client=_openai_client(handler))
    assert len(await e.embed(["hello"])) == 1
    assert attempts == 3


async def test_openai_embedder_does_not_retry_auth_errors(no_backoff: None) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    e = OpenAIEmbedder(model="m", dim=4, api_key="k", client=_openai_client(handler))
    with pytest.raises(openai.AuthenticationError):
        await e.embed(["hello"])
    assert attempts == 1


async def test_openai_embedder_rejects_wrong_dimension(no_backoff: None) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return _embedding_response(json.loads(request.content)["input"], 8)

    e = OpenAIEmbedder(model="m", dim=4, api_key="k", client=_openai_client(handler))
    with pytest.raises(EmbeddingDimensionError):
        await e.embed(["hello"])
    assert attempts == 1


# --- local (fake sentence-transformers) ------------------------------------------


class _FakeSentenceTransformer:
    encode_threads: ClassVar[list[str]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    def get_sentence_embedding_dimension(self) -> int:
        return 3

    def encode(
        self, texts: list[str], normalize_embeddings: bool, show_progress_bar: bool
    ) -> list[list[float]]:
        self.encode_threads.append(threading.current_thread().name)
        return [[1.0, 0.0, 0.0] for _ in texts]


@pytest.fixture
def fake_sentence_transformers(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = _FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return module


async def test_local_embedder_encodes_off_the_event_loop(
    fake_sentence_transformers: types.ModuleType,
) -> None:
    e = LocalEmbedder(model="fake", dim=3)
    assert await e.embed(["a", "b"]) == [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    assert await e.embed([]) == []
    assert _FakeSentenceTransformer.encode_threads[-1] != threading.main_thread().name


def test_local_embedder_rejects_dimension_mismatch(
    fake_sentence_transformers: types.ModuleType,
) -> None:
    with pytest.raises(EmbeddingDimensionError):
        LocalEmbedder(model="fake", dim=384)


# --- factory ---------------------------------------------------------------------


def test_make_embedder_selects_provider(fake_sentence_transformers: types.ModuleType) -> None:
    assert isinstance(
        make_embedder(Settings(embedding_provider="hash", embedding_dim=32)), HashEmbedder
    )
    local = make_embedder(Settings(embedding_provider="local", embedding_dim=3))
    assert isinstance(local, LocalEmbedder)
    oai = make_embedder(Settings(embedding_provider="openai", openai_api_key="sk-x"))
    assert isinstance(oai, OpenAIEmbedder)
    keyless = make_embedder(
        Settings(
            embedding_provider="openai",
            openai_api_key=None,
            embedding_base_url="http://localhost:11434/v1",
        )
    )
    assert isinstance(keyless, OpenAIEmbedder)


def test_make_embedder_openai_requires_key() -> None:
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        make_embedder(Settings(embedding_provider="openai", openai_api_key=None))

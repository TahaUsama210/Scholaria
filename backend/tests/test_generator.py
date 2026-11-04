import json
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import openai
import pytest
from prometheus_client import REGISTRY

from app.core.config import Settings
from app.rag.generator import (
    FakeGenerator,
    OpenAIGenerator,
    StreamEvent,
    citation_payload,
    make_generator,
    parse_citation_markers,
)
from app.schemas.chat import Citation
from tests.helpers import chunk


@dataclass
class Turn:
    role: str
    content: str


async def _collect(
    gen: FakeGenerator | OpenAIGenerator, question: str, chunks: list
) -> list[StreamEvent]:  # type: ignore[type-arg]
    return [ev async for ev in gen.stream(question=question, chunks=chunks)]


# --- citation parsing -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "n", "expected"),
    [
        ("Claim [1].", 3, [1]),
        ("A [2] and B [1] and again [2].", 3, [1, 2]),
        ("Grouped [1, 3] and [2;3].", 3, [1, 2, 3]),
        ("Out of range [0] [4] [99].", 3, []),
        ("Code like arr[0] or x[10] is not a citation.", 3, []),
        ("Adjacent [1][2].", 2, [1, 2]),
        ("No citations at all.", 3, []),
        ("[1]", 0, []),
    ],
)
def test_parse_citation_markers(text: str, n: int, expected: list[int]) -> None:
    assert parse_citation_markers(text, n) == expected


def test_citation_payload_matches_the_wire_schema() -> None:
    c = chunk("x" * 1000, score=0.5, page=None)
    payload = citation_payload(2, c)
    assert set(payload) == set(Citation.model_fields)
    citation = Citation.model_validate(payload)
    assert citation.marker == 2
    assert len(citation.snippet) == 280
    assert citation.chunk_id == str(c.chunk_id)


# --- fake provider ----------------------------------------------------------------


async def test_fake_generator_streams_an_answer_with_valid_citations() -> None:
    chunks = [
        chunk("Overfitting occurs when a model memorises noise. It hurts generalisation."),
        chunk("Dropout and early stopping reduce overfitting in practice."),
        chunk("Third normal form removes transitive dependencies."),
    ]
    events = await _collect(FakeGenerator(), "How do we reduce overfitting?", chunks)
    types = [e.type for e in events]
    assert types[-2:] == ["citations", "done"]
    assert set(types[:-2]) == {"token"} and len(types) > 5

    answer = "".join(str(e.data) for e in events if e.type == "token")
    assert events[-1].data == {"answer": answer}
    cited = events[-2].data
    assert isinstance(cited, list)
    assert [c["marker"] for c in cited] == [1, 2]  # the unrelated 3rd chunk isn't cited
    assert "memorises noise [1]." in answer


async def test_fake_generator_is_deterministic() -> None:
    chunks = [chunk("Backpropagation applies the chain rule in reverse.")]
    a = await _collect(FakeGenerator(), "What is backpropagation?", chunks)
    b = await _collect(FakeGenerator(), "What is backpropagation?", chunks)
    assert a == b


def test_fake_compose_handles_blank_and_long_chunks() -> None:
    assert "no readable text" in FakeGenerator().compose("q", [chunk("   ")])
    long = "word " * 200 + "."
    answer = FakeGenerator().compose("word", [chunk(long)])
    assert "…" in answer and answer.endswith("[1].")


async def test_fake_condense() -> None:
    gen = FakeGenerator()
    assert await gen.condense(question="Why?", history=[]) == "Why?"
    history = [Turn("user", "What is  overfitting?"), Turn("assistant", "It is ...")]
    assert await gen.condense(question="Why does it happen?", history=history) == (
        "Why does it happen? (follow-up to: What is overfitting?)"
    )
    # Only assistant turns, or a repeat of the same question: nothing to resolve.
    assert await gen.condense(question="Q", history=[Turn("assistant", "A")]) == "Q"
    assert await gen.condense(question="Q", history=[Turn("user", "Q")]) == "Q"


# --- openai-compatible (mock transport) --------------------------------------------


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        api_key="sk-test",
        base_url="http://llm.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _sse(*payloads: dict[str, object]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(p)}\n\n" for p in payloads) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _delta(content: str | None) -> dict[str, object]:
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }


def _tokens(kind: str) -> float:
    return REGISTRY.get_sample_value("scholaria_llm_tokens_total", {"kind": kind}) or 0.0


async def test_openai_generator_streams_tokens_and_records_usage() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        usage_chunk = {
            **_delta(None),
            "choices": [],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
        }
        return _sse(_delta("Gradients flow "), _delta(None), _delta("backwards [1]."), usage_chunk)

    before = (_tokens("prompt"), _tokens("completion"))
    gen = OpenAIGenerator(settings=Settings(llm_model="m"), client=_client(handler))
    events = await _collect(gen, "What is backprop?", [chunk("Backprop computes gradients.")])

    assert [e.data for e in events if e.type == "token"] == ["Gradients flow ", "backwards [1]."]
    assert events[-1].data == {"answer": "Gradients flow backwards [1]."}
    assert [c["marker"] for c in events[-2].data] == [1]  # type: ignore[union-attr]
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert requests[0]["model"] == "m"
    assert (_tokens("prompt") - before[0], _tokens("completion") - before[1]) == (11, 3)


async def test_openai_generator_can_skip_stream_usage() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _sse(_delta("ok"))

    gen = OpenAIGenerator(settings=Settings(llm_stream_usage=False), client=_client(handler))
    await _collect(gen, "q", [chunk("c")])
    assert "stream_options" not in requests[0]


async def test_openai_condense_cleans_the_model_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["messages"][0]["role"] == "system"
        assert "User: What is overfitting?" in body["messages"][1]["content"]
        return httpx.Response(200, json={
            "id": "c", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant",
                "content": '"Standalone question: Why does overfitting happen?"'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        })  # fmt: skip

    gen = OpenAIGenerator(settings=Settings(), client=_client(handler))
    history = [Turn("user", "What is overfitting?"), Turn("assistant", "Memorising noise.")]
    out = await gen.condense(question="Why does it happen?", history=history)
    assert out == "Why does overfitting happen?"


async def test_openai_condense_falls_back_to_question_on_empty_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "c", "object": "chat.completion", "created": 0, "model": "m", "choices": [],
        })  # fmt: skip

    gen = OpenAIGenerator(settings=Settings(), client=_client(handler))
    assert await gen.condense(question="Why?", history=[Turn("user", "x")]) == "Why?"


def test_make_generator() -> None:
    assert isinstance(make_generator(Settings(llm_provider="fake")), FakeGenerator)
    assert isinstance(
        make_generator(Settings(llm_provider="openai", llm_api_key="k", openai_api_key=None)),
        OpenAIGenerator,
    )
    # Keyless OpenAI-compatible endpoint (e.g. Ollama) is allowed.
    assert isinstance(
        make_generator(
            Settings(
                llm_provider="openai",
                llm_api_key=None,
                openai_api_key=None,
                llm_base_url="http://localhost:11434/v1",
            )
        ),
        OpenAIGenerator,
    )
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        make_generator(Settings(llm_provider="openai", llm_api_key=None, openai_api_key=None))

"""Answer generation: provider-agnostic streaming with citation parsing.

``OpenAIGenerator`` talks to any OpenAI-compatible chat endpoint (OpenAI,
Groq, Ollama, ...). ``FakeGenerator`` is a deterministic, offline stand-in
that builds an extractive answer from the retrieved chunks, so the whole
stack can be tested and demoed without a paid API.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import BaseMessage
from openai import AsyncOpenAI, omit
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletionMessageParam

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.metrics import LLM_TOKENS
from app.rag.embeddings import hash_tokens
from app.rag.prompts import CHAT_PROMPT, CONDENSE_PROMPT, format_context, format_history
from app.rag.retriever import RetrievedChunk

log = get_logger(__name__)

# "[1]", "[1, 3]" and "[2;4]" all cite; markers outside the context list are dropped.
CITATION_RE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
SNIPPET_CHARS = 280
MAX_QUESTION_CHARS = 2000


@dataclass(frozen=True, slots=True)
class StreamEvent:
    type: str  # "context" | "token" | "citations" | "done"
    data: object


class HistoryTurn(Protocol):
    @property
    def role(self) -> str: ...

    @property
    def content(self) -> str: ...


def citation_payload(marker: int, chunk: RetrievedChunk) -> dict[str, object]:
    """Wire shape of a Citation (see app.schemas.chat.Citation)."""
    return {
        "marker": marker,
        "chunk_id": str(chunk.chunk_id),
        "document_id": str(chunk.document_id),
        "document_title": chunk.document_title,
        "page_number": chunk.page_number,
        "snippet": chunk.content[:SNIPPET_CHARS],
        "score": chunk.score,
    }


def parse_citation_markers(text: str, n_chunks: int) -> list[int]:
    """Sorted, de-duplicated 1-based markers that refer to an existing chunk."""
    found: set[int] = set()
    for match in CITATION_RE.finditer(text):
        for part in re.split(r"[,;]", match.group(1)):
            idx = int(part)
            if 1 <= idx <= n_chunks:
                found.add(idx)
    return sorted(found)


def record_usage(usage: CompletionUsage | None) -> None:
    if usage is None:
        return
    LLM_TOKENS.labels(kind="prompt").inc(usage.prompt_tokens)
    LLM_TOKENS.labels(kind="completion").inc(usage.completion_tokens)


class Generator(ABC):
    """Streams an answer as token events, then the cited chunks, then done."""

    async def stream(
        self, *, question: str, chunks: list[RetrievedChunk]
    ) -> AsyncGenerator[StreamEvent]:
        buf: list[str] = []
        async for delta in self._stream_text(question=question, chunks=chunks):
            buf.append(delta)
            yield StreamEvent(type="token", data=delta)

        answer = "".join(buf)
        markers = parse_citation_markers(answer, len(chunks))
        cited = [citation_payload(i, chunks[i - 1]) for i in markers]
        yield StreamEvent(type="citations", data=cited)
        yield StreamEvent(type="done", data={"answer": answer})
        log.info("generation_complete", answer_chars=len(answer), citations=len(cited))

    @abstractmethod
    def _stream_text(
        self, *, question: str, chunks: list[RetrievedChunk]
    ) -> AsyncIterator[str]: ...

    @abstractmethod
    async def condense(self, *, question: str, history: Sequence[HistoryTurn]) -> str:
        """Rewrite a follow-up into a standalone question for retrieval."""


# --- openai-compatible ----------------------------------------------------


def _to_openai(messages: list[BaseMessage]) -> list[ChatCompletionMessageParam]:
    out: list[ChatCompletionMessageParam] = []
    for m in messages:
        content = m.content if isinstance(m.content, str) else str(m.content)
        if m.type == "system":
            out.append({"role": "system", "content": content})
        elif m.type == "ai":
            out.append({"role": "assistant", "content": content})
        else:
            out.append({"role": "user", "content": content})
    return out


def _clean_condensed(text: str) -> str:
    text = text.strip().strip('"').strip()
    text = re.sub(r"^(standalone question:)\s*", "", text, flags=re.IGNORECASE)
    return text[:MAX_QUESTION_CHARS]


class OpenAIGenerator(Generator):
    def __init__(self, *, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        self._settings = settings
        self._client = client or AsyncOpenAI(
            api_key=settings.resolved_llm_api_key or "unused",  # keyless for e.g. Ollama
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
        )

    async def _stream_text(
        self, *, question: str, chunks: list[RetrievedChunk]
    ) -> AsyncIterator[str]:
        context = format_context([(c.document_title, c.content, c.page_number) for c in chunks])
        messages = CHAT_PROMPT.format_messages(question=question, context=context)

        stream = await self._client.chat.completions.create(
            model=self._settings.llm_model,
            messages=_to_openai(messages),
            temperature=self._settings.llm_temperature,
            max_tokens=self._settings.llm_max_tokens,
            stream=True,
            stream_options={"include_usage": True} if self._settings.llm_stream_usage else omit,
        )
        async for chunk in stream:
            # With include_usage the final chunk carries usage and no choices.
            record_usage(chunk.usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    async def condense(self, *, question: str, history: Sequence[HistoryTurn]) -> str:
        messages = CONDENSE_PROMPT.format_messages(
            question=question,
            history=format_history([(t.role, t.content) for t in history]),
        )
        resp = await self._client.chat.completions.create(
            model=self._settings.llm_model,
            messages=_to_openai(messages),
            temperature=0.0,
            max_tokens=256,
        )
        record_usage(resp.usage)
        text = resp.choices[0].message.content if resp.choices else None
        return _clean_condensed(text or "") or question


# --- fake (offline) ---------------------------------------------------------

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_MAX_SENTENCE_CHARS = 300


def _best_sentence(content: str, query_tokens: set[str]) -> str:
    """The sentence sharing the most tokens with the query (earliest on ties)."""
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(" ".join(content.split())) if s]
    if not sentences:
        return ""
    best = max(sentences, key=lambda s: len(query_tokens & set(hash_tokens(s))))
    if len(best) > _MAX_SENTENCE_CHARS:
        best = best[:_MAX_SENTENCE_CHARS].rsplit(" ", 1)[0] + "…"
    return best.rstrip(".!?;: ")


class FakeGenerator(Generator):
    """Deterministic extractive answers with valid [n] citations. No network."""

    def __init__(self, *, max_sources: int = 3) -> None:
        self._max_sources = max_sources

    def compose(self, question: str, chunks: list[RetrievedChunk]) -> str:
        query_tokens = set(hash_tokens(question))
        parts = []
        for i, chunk in enumerate(chunks[: self._max_sources], start=1):
            sentence = _best_sentence(chunk.content, query_tokens)
            # Always use the top source; others only if they share a term with the question.
            if sentence and (i == 1 or query_tokens & set(hash_tokens(sentence))):
                parts.append(f"{sentence} [{i}].")
        if not parts:
            return "The retrieved passages contain no readable text."
        return "Based on the course materials: " + " ".join(parts)

    async def _stream_text(
        self, *, question: str, chunks: list[RetrievedChunk]
    ) -> AsyncIterator[str]:
        for piece in re.findall(r"\S+\s*", self.compose(question, chunks)):
            yield piece

    async def condense(self, *, question: str, history: Sequence[HistoryTurn]) -> str:
        last_user = next((t.content for t in reversed(history) if t.role == "user"), None)
        if not last_user or last_user.strip() == question.strip():
            return question
        last = " ".join(last_user.split())
        return f"{question} (follow-up to: {last})"[:MAX_QUESTION_CHARS]


def make_generator(settings: Settings) -> Generator:
    match settings.llm_provider:
        case "fake":
            return FakeGenerator()
        case "openai":
            if settings.resolved_llm_api_key is None and settings.llm_base_url is None:
                raise RuntimeError(
                    "LLM_PROVIDER=openai needs LLM_API_KEY (or OPENAI_API_KEY), or LLM_BASE_URL "
                    "for a keyless OpenAI-compatible endpoint such as Ollama."
                )
            return OpenAIGenerator(settings=settings)

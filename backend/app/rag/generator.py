"""Streaming generator with citation parsing."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.core.config import Settings
from app.core.logging import get_logger
from app.rag.prompts import CHAT_PROMPT, format_context
from app.rag.retriever import RetrievedChunk

log = get_logger(__name__)

CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True, slots=True)
class StreamEvent:
    type: str  # "token" | "citations" | "done"
    data: object


class Generator:
    def __init__(self, *, settings: Settings, api_key: str) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(api_key=api_key)

    async def stream(
        self, *, question: str, chunks: list[RetrievedChunk]
    ) -> AsyncIterator[StreamEvent]:
        context = format_context(
            [(c.document_title, c.content, c.page_number) for c in chunks]
        )
        messages = CHAT_PROMPT.format_messages(question=question, context=context)

        # langchain BaseMessage → OpenAI dict — keep it explicit, no langchain runtime.
        oai_messages = [
            {"role": _role(m.type), "content": m.content} for m in messages
        ]

        response = await self._client.chat.completions.create(
            model=self._settings.llm_model,
            messages=oai_messages,  # type: ignore[arg-type]
            temperature=self._settings.llm_temperature,
            max_tokens=self._settings.llm_max_tokens,
            stream=True,
        )

        buf: list[str] = []
        async for chunk in response:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                buf.append(delta)
                yield StreamEvent(type="token", data=delta)

        full_text = "".join(buf)
        cited_indices = sorted({int(m.group(1)) for m in CITATION_RE.finditer(full_text)})
        cited = [
            {
                "marker": idx,
                "chunk_id": str(chunks[idx - 1].chunk_id),
                "document_id": str(chunks[idx - 1].document_id),
                "document_title": chunks[idx - 1].document_title,
                "page_number": chunks[idx - 1].page_number,
                "snippet": chunks[idx - 1].content[:280],
            }
            for idx in cited_indices
            if 1 <= idx <= len(chunks)
        ]
        yield StreamEvent(type="citations", data=cited)
        yield StreamEvent(type="done", data={"answer": full_text})
        log.info("generation_complete", answer_chars=len(full_text), citations=len(cited))


def _role(t: str) -> str:
    return {"system": "system", "human": "user", "ai": "assistant"}.get(t, "user")

"""Optional LLM-as-judge through any OpenAI-compatible chat endpoint.

Two rubric scores on a 1-5 scale, normalised to [0, 1]:

- faithfulness: is every claim in the answer supported by the retrieved passages?
- correctness: does the answer agree with the reference answer's key facts?

Enabled only when ``LLM_PROVIDER=openai`` and a key (``LLM_API_KEY`` or
``OPENAI_API_KEY``) or a keyless ``LLM_BASE_URL`` (e.g. Ollama) is configured.
``EVAL_JUDGE_MODEL`` overrides the model; by default it is ``LLM_MODEL``, which
means the generator grades itself, a known source of bias.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI

SYSTEM_PROMPT = (
    "You are a strict grader for a course-notes question answering system. "
    'Reply with JSON only: {"score": <integer 1-5>, "reason": "<one short sentence>"}.'
)

FAITHFULNESS_PROMPT = """\
Rate how well the ANSWER is supported by the PASSAGES. Ignore citation markers like [1].
5 = every claim is supported by the passages
4 = one minor detail is unsupported
3 = some claims are unsupported
2 = most claims are unsupported
1 = the answer contradicts the passages or is fabricated

QUESTION: {question}

PASSAGES:
{passages}

ANSWER:
{answer}
"""

CORRECTNESS_PROMPT = """\
Rate how well the ANSWER matches the key facts of the REFERENCE answer.
Extra correct detail is fine; missing or wrong key facts are not.
5 = all key facts present and correct
4 = minor omission
3 = partially correct
2 = mostly wrong or missing
1 = wrong, or a refusal when the reference has an answer

QUESTION: {question}

REFERENCE:
{reference}

ANSWER:
{answer}
"""

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class JudgeParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class JudgeScore:
    score: float | None  # normalised to [0, 1]; None when judging failed
    raw: int | None
    reason: str
    error: str | None = None


class ChatClient(Protocol):
    """The slice of AsyncOpenAI the judge uses (lets tests pass a stub)."""

    @property
    def chat(self) -> Any: ...


def parse_judgement(content: str) -> tuple[int, str]:
    """Extract (score 1-5, reason) from a judge reply, tolerating prose around the JSON."""
    match = _JSON_OBJECT_RE.search(content)
    if match is None:
        raise JudgeParseError(f"no JSON object in judge reply: {content[:120]!r}")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise JudgeParseError(f"invalid JSON in judge reply: {exc}") from None
    score = data.get("score") if isinstance(data, dict) else None
    if isinstance(score, str) and score.strip().isdigit():
        score = int(score)
    if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5:
        raise JudgeParseError(f"score must be an integer 1-5, got {score!r}")
    return score, str(data.get("reason", ""))[:500]


def judge_settings_status(settings: object) -> tuple[bool, str]:
    """(enabled, human-readable reason)."""
    provider = getattr(settings, "llm_provider", None)
    if provider != "openai":
        return False, (
            f"LLM_PROVIDER={provider}; set LLM_PROVIDER=openai with LLM_API_KEY/OPENAI_API_KEY "
            "(or a keyless LLM_BASE_URL) to enable the LLM-as-judge"
        )
    if getattr(settings, "resolved_llm_api_key", None) is None and not getattr(
        settings, "llm_base_url", None
    ):
        return False, "LLM_PROVIDER=openai but no LLM_API_KEY/OPENAI_API_KEY or LLM_BASE_URL"
    return True, "enabled"


class Judge:
    def __init__(self, client: ChatClient, *, model: str) -> None:
        self._client = client
        self.model = model

    @classmethod
    def from_settings(cls, settings: Any) -> Judge:
        client = AsyncOpenAI(
            api_key=settings.resolved_llm_api_key or "unused",
            base_url=settings.llm_base_url,
            max_retries=5,  # free tiers rate-limit hard; the SDK honours Retry-After
            timeout=settings.llm_timeout_seconds,
        )
        return cls(client, model=os.environ.get("EVAL_JUDGE_MODEL") or settings.llm_model)

    async def _ask(self, prompt: str) -> JudgeScore:
        try:
            resp = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=200,
            )
            raw, reason = parse_judgement(resp.choices[0].message.content or "")
        except Exception as exc:  # one bad call must not sink the whole eval run
            return JudgeScore(None, None, "", error=f"{type(exc).__name__}: {exc}"[:300])
        return JudgeScore((raw - 1) / 4, raw, reason)

    async def faithfulness(
        self, *, question: str, answer: str, passages: Sequence[str]
    ) -> JudgeScore:
        rendered = "\n\n".join(f"[{i}] {p.strip()}" for i, p in enumerate(passages, start=1))
        return await self._ask(
            FAITHFULNESS_PROMPT.format(question=question, passages=rendered, answer=answer)
        )

    async def correctness(self, *, question: str, answer: str, reference: str) -> JudgeScore:
        return await self._ask(
            CORRECTNESS_PROMPT.format(question=question, reference=reference, answer=answer)
        )

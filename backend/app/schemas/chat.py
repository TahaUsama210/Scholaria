from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_HISTORY_TURNS = 10


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    course_code: str | None = Field(default=None, max_length=64)
    history: list[ChatTurn] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be blank")
        return v

    @field_validator("course_code")
    @classmethod
    def _blank_course_is_none(cls, v: str | None) -> str | None:
        return (v or "").strip() or None


class Citation(BaseModel):
    """Wire shape of `context` / `citations` SSE event items."""

    marker: int
    chunk_id: str
    document_id: str
    document_title: str
    page_number: int | None
    snippet: str
    score: float


class ChatErrorEvent(BaseModel):
    """Data of the `error` SSE event: a user-safe message plus the request id."""

    message: str
    request_id: str

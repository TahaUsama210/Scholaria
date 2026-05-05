from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    course_code: str | None = Field(default=None, max_length=64)
    conversation_id: str | None = None


class Citation(BaseModel):
    marker: int
    chunk_id: str
    document_id: str
    document_title: str
    page_number: int | None
    snippet: str


class ChatResponse(BaseModel):
    """Used only for the non-streaming fallback endpoint."""
    answer: str
    citations: list[Citation]

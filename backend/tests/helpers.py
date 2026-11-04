"""Test utilities: PDF generation, SSE parsing, chunk factories."""

import json
from dataclasses import dataclass
from io import BytesIO
from uuid import UUID, uuid4

from reportlab.lib.pagesizes import LETTER  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from app.rag.retriever import RetrievedChunk


def make_pdf(pages: list[str]) -> bytes:
    """A real multi-page PDF; each string becomes one page (wrapped at ~90 chars)."""
    out = BytesIO()
    pdf = canvas.Canvas(out, pagesize=LETTER)
    _, height = LETTER
    for body in pages:
        text = pdf.beginText(72, height - 72)
        text.setFont("Helvetica", 10)
        line: list[str] = []
        for word in body.split():
            if sum(len(w) + 1 for w in line) + len(word) > 90:
                text.textLine(" ".join(line))
                line = []
            line.append(word)
        if line:
            text.textLine(" ".join(line))
        pdf.drawText(text)
        pdf.showPage()
    pdf.save()
    return out.getvalue()


@dataclass(frozen=True)
class SSEEvent:
    event: str
    data: object


def parse_sse(body: str) -> list[SSEEvent]:
    events: list[SSEEvent] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        name, data_lines = "message", []
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                name = value
            elif field == "data":
                data_lines.append(value)
        if data_lines:
            events.append(SSEEvent(name, json.loads("\n".join(data_lines))))
    return events


def chunk(
    content: str = "x",
    *,
    score: float = 0.0,
    title: str = "Doc",
    page: int | None = 1,
    chunk_id: UUID | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id or uuid4(),
        document_id=uuid4(),
        document_title=title,
        page_number=page,
        content=content,
        score=score,
    )

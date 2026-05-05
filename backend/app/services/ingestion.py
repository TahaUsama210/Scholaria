"""Document ingestion service.

End-to-end: PDF bytes -> rows in the database with embeddings.

For a portfolio project, FastAPI BackgroundTasks is fine. In production this
would move to a Redis-backed queue (Arq) so retries and progress are durable.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
from uuid import UUID

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Chunk, Document, DocumentStatus
from app.db.session import async_session_factory
from app.rag.chunker import TokenChunker
from app.rag.embeddings import make_embedder

log = get_logger(__name__)


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_pages(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """Returns [(page_number, text), ...] — page_number is 1-indexed."""
    reader = PdfReader(BytesIO(pdf_bytes))
    out: list[tuple[int, str]] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pypdf has known parser quirks; skip the page
            log.warning("pdf_page_extract_failed", page=i, error=str(exc))
            text = ""
        if text.strip():
            out.append((i, text))
    return out


async def create_pending_document(
    session: AsyncSession,
    *,
    title: str,
    filename: str,
    course_code: str | None,
    content_hash: str,
) -> Document:
    existing = await session.scalar(
        select(Document).where(Document.content_hash == content_hash)
    )
    if existing:
        return existing
    doc = Document(
        title=title,
        source_filename=filename,
        course_code=course_code,
        content_hash=content_hash,
        status=DocumentStatus.PENDING,
    )
    session.add(doc)
    await session.flush()
    return doc


async def ingest_document(document_id: UUID, pdf_bytes: bytes) -> None:
    """Background job: chunk + embed + persist. Owns its own session."""
    settings = get_settings()
    chunker = TokenChunker(
        chunk_size_tokens=settings.chunk_size_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
    )
    embedder = make_embedder(settings)

    async with async_session_factory() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            log.error("ingest_missing_document", document_id=str(document_id))
            return
        doc.status = DocumentStatus.PROCESSING
        await session.commit()

        try:
            pages = extract_pages(pdf_bytes)
            doc.page_count = len(pages)

            chunks = [
                chunk
                for page_num, text in pages
                for chunk in chunker.split(text, page_number=page_num)
            ]
            if not chunks:
                raise ValueError("No extractable text in PDF.")

            vectors = await embedder.embed([c.text for c in chunks])

            for idx, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True)):
                session.add(
                    Chunk(
                        document_id=doc.id,
                        chunk_index=idx,
                        page_number=chunk.page_number,
                        content=chunk.text,
                        token_count=chunk.token_count,
                        embedding=vec,
                    )
                )
            doc.chunk_count = len(chunks)
            doc.status = DocumentStatus.READY
            await session.commit()
            log.info(
                "ingest_done",
                document_id=str(doc.id),
                pages=doc.page_count,
                chunks=doc.chunk_count,
            )
        except Exception as exc:
            await session.rollback()
            doc = await session.get(Document, document_id)
            if doc is not None:
                doc.status = DocumentStatus.FAILED
                doc.error_message = str(exc)[:1000]
                await session.commit()
            log.exception("ingest_failed", document_id=str(document_id))

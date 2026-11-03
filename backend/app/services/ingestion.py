"""Document ingestion service.

End-to-end: PDF bytes -> rows in the database with embeddings.

For a portfolio project, FastAPI BackgroundTasks is fine. In production this
would move to a Redis-backed queue (Arq) so retries and progress are durable.
Until then, a pending/processing document that goes stale (server restarted
mid-ingest) can be revived by uploading the same file again.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import UUID

from opentelemetry.trace import Status, StatusCode
from pypdf import PasswordType, PdfReader
from pypdf.errors import PyPdfError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.metrics import INGEST_DOCUMENTS, INGEST_DURATION
from app.core.tracing import tracer
from app.db.models import Chunk, Document, DocumentStatus
from app.db.session import async_session_factory
from app.rag.chunker import Chunk as TextChunk
from app.rag.chunker import TokenChunker
from app.rag.embeddings import Embedder, get_embedder

log = get_logger(__name__)

PDF_MAGIC = b"%PDF-"
# Readers accept the header anywhere in the first KiB (some generators prepend junk).
_PDF_HEADER_WINDOW = 1024
INTERNAL_FAILURE_MESSAGE = "Ingestion failed due to an internal error."


class IngestionError(Exception):
    """An ingestion failure whose message is safe to show to users."""


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def looks_like_pdf(data: bytes) -> bool:
    return PDF_MAGIC in data[:_PDF_HEADER_WINDOW]


def _open_pdf(pdf_bytes: bytes) -> PdfReader:
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            raise IngestionError("PDF is password-protected.")
        return reader
    except IngestionError:
        raise
    except Exception as exc:  # pypdf raises a zoo of types on malformed input
        raise IngestionError("Could not read PDF: the file is corrupt or unsupported.") from exc


def extract_pages(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """Returns [(page_number, text), ...] for pages with text; page_number is 1-indexed."""
    return _extract(pdf_bytes)[1]


def _extract(pdf_bytes: bytes) -> tuple[int, list[tuple[int, str]]]:
    reader = _open_pdf(pdf_bytes)
    try:
        pages = list(reader.pages)
    except Exception as exc:
        raise IngestionError("Could not read PDF: the file is corrupt or unsupported.") from exc
    out: list[tuple[int, str]] = []
    for i, page in enumerate(pages, start=1):
        try:
            text = page.extract_text() or ""
        except (PyPdfError, ValueError, KeyError, TypeError) as exc:
            # pypdf has known parser quirks; skip the page rather than the document.
            log.warning("pdf_page_extract_failed", page=i, error=repr(exc))
            text = ""
        if text.strip():
            out.append((i, text))
    return len(pages), out


def _extract_and_chunk(pdf_bytes: bytes, chunker: TokenChunker) -> tuple[int, list[TextChunk]]:
    page_count, pages = _extract(pdf_bytes)
    chunks = [c for page_num, text in pages for c in chunker.split(text, page_number=page_num)]
    return page_count, chunks


# --- upload registration ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class UploadRegistration:
    document: Document
    should_ingest: bool
    message: str


def _is_stale(doc: Document, stale_after_seconds: int) -> bool:
    if doc.status not in (DocumentStatus.PENDING, DocumentStatus.PROCESSING):
        return False
    return doc.updated_at < datetime.now(UTC) - timedelta(seconds=stale_after_seconds)


async def register_upload(
    session: AsyncSession,
    *,
    title: str,
    filename: str,
    course_code: str | None,
    content_hash: str,
    settings: Settings | None = None,
) -> UploadRegistration:
    """Create (or revive) the document row for an upload. The caller commits.

    Content-hash dedup: a ready or in-flight duplicate is returned as-is; a failed
    or stale one is reset for re-ingestion; a concurrent duplicate insert that
    loses the unique-constraint race resolves to the winner's row.
    """
    settings = settings or get_settings()
    stmt = select(Document).where(Document.content_hash == content_hash)
    # Row lock: two concurrent retries of a failed document must not both re-ingest.
    existing = await session.scalar(stmt.with_for_update())

    if existing is None:
        doc = Document(
            title=title,
            source_filename=filename,
            course_code=course_code,
            content_hash=content_hash,
            status=DocumentStatus.PENDING,
        )
        session.add(doc)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            winner = await session.scalar(stmt)
            if winner is None:  # pragma: no cover - the conflicting row vanished again
                raise
            log.info("upload_duplicate_race", document_id=str(winner.id))
            return UploadRegistration(winner, False, _duplicate_message(winner))
        return UploadRegistration(doc, True, "Ingestion started.")

    if existing.status == DocumentStatus.FAILED or _is_stale(
        existing, settings.ingest_stale_after_seconds
    ):
        previous = existing.status
        await session.execute(delete(Chunk).where(Chunk.document_id == existing.id))
        existing.title = title
        existing.source_filename = filename
        existing.course_code = course_code
        existing.status = DocumentStatus.PENDING
        existing.error_message = None
        existing.page_count = 0
        existing.chunk_count = 0
        await session.flush()
        log.info("upload_retry", document_id=str(existing.id), previous_status=previous)
        return UploadRegistration(
            existing, True, "Previous ingestion did not finish; re-ingesting."
        )

    return UploadRegistration(existing, False, _duplicate_message(existing))


def _duplicate_message(doc: Document) -> str:
    if doc.status == DocumentStatus.READY:
        return "Document already ingested (matched by content hash)."
    return "Document is already being ingested (matched by content hash)."


async def create_pending_document(
    session: AsyncSession,
    *,
    title: str,
    filename: str,
    course_code: str | None,
    content_hash: str,
) -> Document:
    """Backwards-compatible wrapper used by the seed script."""
    reg = await register_upload(
        session,
        title=title,
        filename=filename,
        course_code=course_code,
        content_hash=content_hash,
    )
    return reg.document


# --- background ingestion ---------------------------------------------------


async def ingest_document(
    document_id: UUID,
    pdf_bytes: bytes,
    *,
    embedder: Embedder | None = None,
    settings: Settings | None = None,
) -> None:
    """Background job: extract, chunk, embed, persist. Owns its own session.

    Idempotent: existing chunks for the document are replaced, so re-running it
    (retry, seed script) never duplicates rows.
    """
    settings = settings or get_settings()
    embedder = embedder or get_embedder()
    chunker = TokenChunker(
        chunk_size_tokens=settings.chunk_size_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
    )
    start = time.perf_counter()

    with tracer.start_as_current_span("ingest.document") as span:
        span.set_attribute("document.id", str(document_id))
        async with async_session_factory() as session:
            doc = await session.get(Document, document_id)
            if doc is None:
                log.error("ingest_missing_document", document_id=str(document_id))
                return
            doc.status = DocumentStatus.PROCESSING
            doc.error_message = None
            await session.commit()

            try:
                # pypdf + tiktoken are CPU-bound: keep them off the event loop.
                page_count, chunks = await asyncio.to_thread(_extract_and_chunk, pdf_bytes, chunker)
                if not chunks:
                    raise IngestionError(
                        "No extractable text in PDF (scanned documents need OCR first)."
                    )
                vectors = await _embed_with_heartbeat(
                    session, doc, embedder, [c.text for c in chunks], settings.embedding_batch_size
                )

                await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
                session.add_all(
                    Chunk(
                        document_id=document_id,
                        chunk_index=idx,
                        page_number=chunk.page_number,
                        content=chunk.text,
                        token_count=chunk.token_count,
                        embedding=vec,
                    )
                    for idx, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True))
                )
                doc.page_count = page_count
                doc.chunk_count = len(chunks)
                doc.status = DocumentStatus.READY
                await session.commit()
            except Exception as exc:
                await session.rollback()
                message = str(exc) if isinstance(exc, IngestionError) else INTERNAL_FAILURE_MESSAGE
                await _mark_failed(session, document_id, message)
                INGEST_DOCUMENTS.labels(status="failed").inc()
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                log.exception("ingest_failed", document_id=str(document_id))
                return
            finally:
                INGEST_DURATION.observe(time.perf_counter() - start)

        INGEST_DOCUMENTS.labels(status="ready").inc()
        span.set_attribute("document.pages", page_count)
        span.set_attribute("document.chunks", len(chunks))
        log.info("ingest_done", document_id=str(document_id), pages=page_count, chunks=len(chunks))


async def _embed_with_heartbeat(
    session: AsyncSession,
    doc: Document,
    embedder: Embedder,
    texts: list[str],
    batch_size: int,
) -> list[list[float]]:
    """Embed in batches, bumping ``updated_at`` between them.

    A long ingest must not look abandoned: register_upload treats a pending or
    processing document idle past INGEST_STALE_AFTER_SECONDS as dead and re-ingests it.
    """
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        if start:
            doc.updated_at = datetime.now(UTC)
            await session.commit()
        vectors.extend(await embedder.embed(texts[start : start + batch_size]))
    return vectors


async def _mark_failed(session: AsyncSession, document_id: UUID, message: str) -> None:
    try:
        doc = await session.get(Document, document_id)
        if doc is not None:  # it may have been deleted mid-ingest
            doc.status = DocumentStatus.FAILED
            doc.error_message = message[:1000]
            await session.commit()
    except Exception:
        log.exception("ingest_mark_failed_failed", document_id=str(document_id))

from pathlib import PurePath
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.deps import DBDep
from app.core.logging import get_logger
from app.core.rate_limit import limiter, upload_limit
from app.db.models import Document
from app.schemas.document import DocumentList, DocumentRead, DocumentUploadResponse
from app.services.ingestion import hash_bytes, ingest_document, looks_like_pdf, register_upload

log = get_logger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

# Browsers and curl sometimes label PDFs generically; the magic bytes are authoritative.
_ACCEPTED_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
    "binary/octet-stream",
}


def _too_large(limit_bytes: int) -> HTTPException:
    return HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE,
        f"PDF exceeds the {limit_bytes // (1024 * 1024)} MB limit.",
    )


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


@router.post(
    "",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"description": "Empty file."},
        413: {"description": "File exceeds the upload limit."},
        415: {"description": "Not a PDF."},
        429: {"description": "Rate limit exceeded."},
    },
)
@limiter.limit(upload_limit)
async def upload_document(
    request: Request,
    db: DBDep,
    background: BackgroundTasks,
    file: UploadFile,
    title: Annotated[str | None, Form(max_length=512)] = None,
    course_code: Annotated[str | None, Form(max_length=64)] = None,
) -> DocumentUploadResponse:
    limit = get_settings().max_upload_bytes
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type and content_type not in _ACCEPTED_CONTENT_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Only PDF is supported (got {content_type}).",
        )
    # The multipart parser already knows the size: don't pull an oversized file into memory.
    if file.size is not None and file.size > limit:
        raise _too_large(limit)
    pdf_bytes = await file.read(limit + 1)
    if len(pdf_bytes) > limit:
        raise _too_large(limit)
    if not pdf_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file.")
    if not looks_like_pdf(pdf_bytes):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "File is not a PDF (missing %PDF- header)."
        )

    filename = PurePath((file.filename or "").replace("\\", "/")).name[:512] or "upload.pdf"
    registration = await register_upload(
        db,
        title=_clean(title) or filename,
        filename=filename,
        course_code=_clean(course_code),
        content_hash=hash_bytes(pdf_bytes),
    )
    # Commit before scheduling: the ingest task opens its own session and must see the
    # row (the request-scoped session would otherwise commit only after the response).
    await db.commit()
    if registration.should_ingest:
        background.add_task(ingest_document, registration.document.id, pdf_bytes)

    return DocumentUploadResponse(
        document=DocumentRead.model_validate(registration.document),
        message=registration.message,
    )


@router.get("", response_model=DocumentList)
async def list_documents(
    db: DBDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    course_code: Annotated[str | None, Query(max_length=64)] = None,
) -> DocumentList:
    count_stmt = select(func.count(Document.id))
    stmt = select(Document).order_by(Document.created_at.desc(), Document.id)
    if course_code:
        count_stmt = count_stmt.where(Document.course_code == course_code)
        stmt = stmt.where(Document.course_code == course_code)
    total = await db.scalar(count_stmt)
    rows = (await db.scalars(stmt.limit(limit).offset(offset))).all()
    return DocumentList(items=[DocumentRead.model_validate(r) for r in rows], total=total or 0)


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(document_id: UUID, db: DBDep) -> DocumentRead:
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    return DocumentRead.model_validate(doc)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: UUID, db: DBDep) -> None:
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    await db.delete(doc)  # chunks go via ON DELETE CASCADE (passive_deletes)
    # Commit before responding so a follow-up GET can't still see the document.
    await db.commit()
    log.info("document_deleted", document_id=str(document_id))

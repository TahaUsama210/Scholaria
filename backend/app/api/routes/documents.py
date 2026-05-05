from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select

from app.core.deps import DBDep
from app.core.logging import get_logger
from app.db.models import Document, DocumentStatus
from app.schemas.document import DocumentList, DocumentRead, DocumentUploadResponse
from app.services.ingestion import (
    create_pending_document,
    hash_bytes,
    ingest_document,
)

log = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

_MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post(
    "",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    db: DBDep,
    background: BackgroundTasks,
    file: UploadFile,
    title: str | None = Form(default=None),
    course_code: str | None = Form(default=None),
) -> DocumentUploadResponse:
    if file.content_type not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Only PDF is supported (got {file.content_type}).",
        )
    pdf_bytes = await file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file.")
    if len(pdf_bytes) > _MAX_PDF_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"PDF exceeds {_MAX_PDF_BYTES // (1024 * 1024)} MB limit.",
        )

    doc = await create_pending_document(
        db,
        title=title or (file.filename or "Untitled"),
        filename=file.filename or "upload.pdf",
        course_code=course_code,
        content_hash=hash_bytes(pdf_bytes),
    )
    is_new = doc.status == DocumentStatus.PENDING and doc.chunk_count == 0
    if is_new:
        background.add_task(ingest_document, doc.id, pdf_bytes)
        message = "Ingestion started."
    else:
        message = "Document already ingested (matched by content hash)."

    return DocumentUploadResponse(
        document=DocumentRead.model_validate(doc), message=message
    )


@router.get("", response_model=DocumentList)
async def list_documents(db: DBDep, limit: int = 50, offset: int = 0) -> DocumentList:
    total = await db.scalar(select(func.count(Document.id)))
    stmt = select(Document).order_by(Document.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.scalars(stmt)).all()
    return DocumentList(
        items=[DocumentRead.model_validate(r) for r in rows], total=total or 0
    )


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
    await db.delete(doc)

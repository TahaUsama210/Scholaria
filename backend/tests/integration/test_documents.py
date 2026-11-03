import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentStatus
from app.db.session import async_session_factory
from app.rag.embeddings import Embedder
from app.services import ingestion
from app.services.ingestion import (
    INTERNAL_FAILURE_MESSAGE,
    create_pending_document,
    hash_bytes,
    ingest_document,
    register_upload,
)
from tests.helpers import make_pdf
from tests.integration.conftest import sql

PAGES = [
    "Backpropagation computes the gradient of the loss with respect to every weight.",
    "Overfitting occurs when a model memorises noise in the training data.",
    "Dropout randomly zeroes activations during training to regularise the network.",
]


async def upload(
    client: AsyncClient, pdf: bytes, *, name: str = "notes.pdf", **form: str
) -> dict[str, Any]:
    r = await client.post(
        "/api/documents", files={"file": (name, pdf, "application/pdf")}, data=form
    )
    assert r.status_code == 202, r.text
    body: dict[str, Any] = r.json()
    return body


def chunk_count(document_id: str | UUID) -> int:
    return int(sql("SELECT count(*) FROM chunks WHERE document_id = %s", str(document_id))[0][0])  # type: ignore[call-overload]


async def test_upload_is_visible_to_the_ingest_task_and_becomes_ready(client: AsyncClient) -> None:
    # Regression: the row used to be committed only after the response, so the
    # background task couldn't see it and the document stayed "pending" forever.
    body = await upload(client, make_pdf(PAGES), title="ML notes", course_code="CS480")
    assert body["message"] == "Ingestion started."
    doc = (await client.get(f"/api/documents/{body['document']['id']}")).json()
    assert doc["status"] == "ready", doc
    assert doc["title"] == "ML notes" and doc["course_code"] == "CS480"
    assert doc["page_count"] == 3
    assert doc["chunk_count"] == 3 == chunk_count(doc["id"])
    assert doc["error_message"] is None


async def test_list_get_filter_and_paginate(client: AsyncClient) -> None:
    a = await upload(client, make_pdf(["Normal forms"]), name="db.pdf", course_code="CS348")
    b = await upload(client, make_pdf(["Scheduling"]), name="os.pdf", course_code="CS350")

    listing = (await client.get("/api/documents")).json()
    assert listing["total"] == 2
    assert [d["id"] for d in listing["items"]] == [b["document"]["id"], a["document"]["id"]]
    assert listing["items"][1]["title"] == "db.pdf"  # defaults to the filename

    filtered = (await client.get("/api/documents?course_code=CS348")).json()
    assert filtered["total"] == 1 and filtered["items"][0]["id"] == a["document"]["id"]
    page = (await client.get("/api/documents?limit=1&offset=1")).json()
    assert page["total"] == 2 and [d["id"] for d in page["items"]] == [a["document"]["id"]]

    assert (await client.get(f"/api/documents/{uuid4()}")).status_code == 404


async def test_document_chunks_are_never_lazy_loaded(client: AsyncClient, db: AsyncSession) -> None:
    body = await upload(client, make_pdf(PAGES))
    doc = await db.get(Document, UUID(body["document"]["id"]))
    assert doc is not None
    with pytest.raises(InvalidRequestError):
        _ = doc.chunks  # lazy="raise": listing documents must not drag in embeddings


async def test_delete_cascades_to_chunks(client: AsyncClient) -> None:
    doc_id = (await upload(client, make_pdf(PAGES)))["document"]["id"]
    assert chunk_count(doc_id) == 3

    assert (await client.delete(f"/api/documents/{doc_id}")).status_code == 204
    assert chunk_count(doc_id) == 0
    assert (await client.get(f"/api/documents/{doc_id}")).status_code == 404
    assert (await client.delete(f"/api/documents/{doc_id}")).status_code == 404


async def test_duplicate_upload_returns_the_existing_document(client: AsyncClient) -> None:
    pdf = make_pdf(PAGES)
    first = await upload(client, pdf)
    second = await upload(client, pdf, name="copy.pdf")
    assert second["document"]["id"] == first["document"]["id"]
    assert second["message"] == "Document already ingested (matched by content hash)."
    assert sql("SELECT count(*) FROM documents") == [(1,)]
    assert chunk_count(first["document"]["id"]) == 3


class _FailingEmbedder(Embedder):
    dim = 256

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise ConnectionError("embedding service at 10.0.0.5 refused")


async def test_failed_document_is_reset_and_reingested(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = make_pdf(PAGES)
    with monkeypatch.context() as m:
        m.setattr(ingestion, "get_embedder", lambda: _FailingEmbedder())
        doc_id = (await upload(client, pdf))["document"]["id"]
    failed = (await client.get(f"/api/documents/{doc_id}")).json()
    assert failed["status"] == "failed"
    assert failed["error_message"] == INTERNAL_FAILURE_MESSAGE  # no internal detail leaked

    retry = await upload(client, pdf, title="Retried")
    assert retry["document"]["id"] == doc_id
    assert retry["message"] == "Previous ingestion did not finish; re-ingesting."
    ready = (await client.get(f"/api/documents/{doc_id}")).json()
    assert ready["status"] == "ready" and ready["title"] == "Retried"
    assert ready["error_message"] is None
    assert ready["chunk_count"] == 3 == chunk_count(doc_id)


@pytest.mark.parametrize(
    ("pdf", "message"),
    [
        (b"%PDF-1.4\nnot actually a pdf", "corrupt"),
        (make_pdf(["", ""]), "No extractable text"),
    ],
)
async def test_unusable_pdfs_fail_with_a_user_facing_message(
    client: AsyncClient, pdf: bytes, message: str
) -> None:
    doc_id = (await upload(client, pdf))["document"]["id"]
    doc = (await client.get(f"/api/documents/{doc_id}")).json()
    assert doc["status"] == "failed"
    assert message in doc["error_message"]


async def test_concurrent_duplicate_uploads_resolve_to_one_document(client: AsyncClient) -> None:
    pdf = make_pdf(["Concurrency control with two-phase locking."])
    results = await asyncio.gather(upload(client, pdf), upload(client, pdf))
    assert results[0]["document"]["id"] == results[1]["document"]["id"]
    assert sql("SELECT count(*) FROM documents") == [(1,)]


async def test_unique_violation_race_returns_the_winner() -> None:
    """Deterministic version of the race: the loser blocks on the unique index."""
    kwargs = {"title": "t", "filename": "f.pdf", "course_code": None, "content_hash": "h" * 64}
    async with async_session_factory() as s1, async_session_factory() as s2:
        winner = await register_upload(s1, **kwargs)  # type: ignore[arg-type]
        assert winner.should_ingest
        loser_task = asyncio.create_task(register_upload(s2, **kwargs))  # type: ignore[arg-type]
        await asyncio.sleep(0.3)
        assert not loser_task.done()  # waiting on s1's uncommitted insert
        await s1.commit()
        loser = await loser_task
        assert loser.document.id == winner.document.id
        assert not loser.should_ingest
    assert sql("SELECT count(*) FROM documents") == [(1,)]


async def _insert_document(pdf: bytes, *, status: str, age: timedelta) -> UUID:
    async with async_session_factory() as s:
        doc = Document(
            title="old",
            source_filename="old.pdf",
            content_hash=hash_bytes(pdf),
            status=status,
            updated_at=datetime.now(UTC) - age,
        )
        s.add(doc)
        await s.commit()
        return doc.id


async def test_stale_in_flight_document_is_revived(client: AsyncClient) -> None:
    pdf = make_pdf(PAGES)
    doc_id = await _insert_document(pdf, status=DocumentStatus.PROCESSING, age=timedelta(hours=2))
    body = await upload(client, pdf)
    assert body["document"]["id"] == str(doc_id)
    assert body["message"].startswith("Previous ingestion did not finish")
    assert (await client.get(f"/api/documents/{doc_id}")).json()["status"] == "ready"


async def test_fresh_in_flight_document_is_not_reingested(client: AsyncClient) -> None:
    pdf = make_pdf(PAGES)
    doc_id = await _insert_document(pdf, status=DocumentStatus.PROCESSING, age=timedelta(0))
    body = await upload(client, pdf)
    assert body["document"]["id"] == str(doc_id)
    assert body["message"] == "Document is already being ingested (matched by content hash)."
    assert (await client.get(f"/api/documents/{doc_id}")).json()["status"] == "processing"


async def test_ingest_is_idempotent_for_the_seed_script() -> None:
    pdf = make_pdf(PAGES)
    async with async_session_factory() as s:
        doc = await create_pending_document(
            s, title="Seed", filename="seed.pdf", course_code="CS480", content_hash=hash_bytes(pdf)
        )
        await s.commit()
    await ingest_document(doc.id, pdf)
    await ingest_document(doc.id, pdf)  # the seed script re-runs on every boot
    assert chunk_count(doc.id) == 3
    assert sql("SELECT status, chunk_count FROM documents") == [("ready", 3)]


async def test_ingest_of_a_missing_document_is_a_no_op() -> None:
    await ingest_document(uuid4(), make_pdf(PAGES))
    assert sql("SELECT count(*) FROM documents") == [(0,)]


async def test_readyz_with_a_live_database(client: AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"database": "ok"}}

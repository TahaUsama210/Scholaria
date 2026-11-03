from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from app.rag.chunker import TokenChunker
from app.services.ingestion import (
    IngestionError,
    _extract,
    _extract_and_chunk,
    extract_pages,
    hash_bytes,
    looks_like_pdf,
)
from tests.helpers import make_pdf


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"%PDF-1.7\n...", True),
        (b"\xef\xbb\xbf  junk\n%PDF-1.4", True),  # header within the first KiB
        (b" " * 2000 + b"%PDF-1.4", False),
        (b"<html>%PDF</html>", False),
        (b"", False),
    ],
)
def test_looks_like_pdf(data: bytes, expected: bool) -> None:
    assert looks_like_pdf(data) is expected


def test_extract_counts_every_page_but_returns_only_text_pages() -> None:
    pdf = make_pdf(["First page text.", "", "Third page text."])
    page_count, pages = _extract(pdf)
    assert page_count == 3
    assert [n for n, _ in pages] == [1, 3]
    assert "Third page" in pages[1][1]
    assert extract_pages(pdf) == pages


def test_extract_and_chunk_keeps_page_numbers() -> None:
    pdf = make_pdf(["alpha " * 50, "beta " * 50])
    page_count, chunks = _extract_and_chunk(
        pdf, TokenChunker(chunk_size_tokens=32, overlap_tokens=4)
    )
    assert page_count == 2
    assert {c.page_number for c in chunks} == {1, 2}
    assert all(c.token_count <= 32 for c in chunks)


def test_corrupt_pdf_raises_a_user_facing_error() -> None:
    with pytest.raises(IngestionError, match="corrupt"):
        _extract(b"%PDF-1.4\nthis is not really a pdf")


def test_password_protected_pdf_raises_a_user_facing_error() -> None:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(["secret"]))))
    writer.encrypt(user_password="pw", owner_password="owner", algorithm="RC4-128")
    out = BytesIO()
    writer.write(out)
    with pytest.raises(IngestionError, match="password"):
        _extract(out.getvalue())


def test_hash_is_sha256_hex() -> None:
    assert hash_bytes(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


async def test_long_embeds_heartbeat_so_the_document_never_looks_stale() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.rag.embeddings import HashEmbedder
    from app.services.ingestion import _embed_with_heartbeat

    class Session:
        commits = 0

        async def commit(self) -> None:
            self.commits += 1

    session = Session()
    long_ago = datetime(2000, 1, 1, tzinfo=UTC)
    doc = SimpleNamespace(updated_at=long_ago)
    vectors = await _embed_with_heartbeat(
        session,  # type: ignore[arg-type]
        doc,  # type: ignore[arg-type]
        HashEmbedder(dim=8),
        [f"chunk {i}" for i in range(5)],
        2,
    )
    assert len(vectors) == 5 and all(len(v) == 8 for v in vectors)
    assert session.commits == 2  # between batches 1-2 and 2-3, not before the first
    assert doc.updated_at > long_ago

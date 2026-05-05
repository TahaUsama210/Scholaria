"""Generate two synthetic course PDFs and ingest them through the real pipeline.

Used both for demo bootstrapping and as the substrate for the Ragas eval gate.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from reportlab.lib.pagesizes import LETTER  # type: ignore[import-not-found]
from reportlab.pdfgen import canvas  # type: ignore[import-not-found]

from app.db.session import async_session_factory
from app.services.ingestion import (
    create_pending_document,
    hash_bytes,
    ingest_document,
)

CONTENT: dict[str, list[tuple[str, str]]] = {
    "CS480 — Intro to Machine Learning.pdf": [
        (
            "1. Backpropagation",
            "Backpropagation is the algorithm that computes the gradient of the loss "
            "function with respect to every parameter of a neural network. It applies "
            "the chain rule in reverse, walking from the output layer back to the "
            "input. The forward pass computes activations and stores them; the "
            "backward pass reuses those activations so the total cost is on the same "
            "order as one forward pass. This is what makes training deep networks "
            "tractable.",
        ),
        (
            "2. Overfitting",
            "Overfitting occurs when a model captures noise specific to the training "
            "set rather than the underlying signal. Classic signs include training "
            "loss continuing to decrease while validation loss begins to rise, and a "
            "widening gap between training and held-out performance. Common remedies "
            "are regularisation (L1, L2, dropout), early stopping, data augmentation, "
            "and reducing model capacity.",
        ),
        (
            "3. Bias–Variance Tradeoff",
            "Total expected error decomposes into bias squared, variance, and "
            "irreducible noise. High-bias models underfit; high-variance models "
            "overfit. Increasing model capacity typically lowers bias and raises "
            "variance — the goal is to find the sweet spot for the given dataset "
            "size.",
        ),
    ],
    "CS348 — Database Systems.pdf": [
        (
            "1. Third Normal Form",
            "A relation is in third normal form when it is in second normal form and "
            "every non-prime attribute is non-transitively dependent on every "
            "candidate key. Equivalently, no non-prime attribute depends on another "
            "non-prime attribute. 3NF eliminates most redundancy while remaining "
            "lossless and dependency-preserving.",
        ),
        (
            "2. ACID",
            "ACID describes the four guarantees a transactional database provides. "
            "Atomicity: all operations in a transaction succeed or none do. "
            "Consistency: invariants are preserved across transactions. Isolation: "
            "concurrent transactions appear to execute serially. Durability: once "
            "committed, the change survives subsequent failures.",
        ),
        (
            "3. Indexes",
            "B-tree indexes accelerate range and equality lookups in O(log n) and "
            "are the default for most relational engines. Hash indexes are O(1) for "
            "equality but cannot serve range scans. For approximate nearest neighbour "
            "search over embeddings, HNSW indexes are now the production default.",
        ),
    ],
}


def make_pdf(sections: list[tuple[str, str]]) -> bytes:
    out = BytesIO()
    pdf = canvas.Canvas(out, pagesize=LETTER)
    width, height = LETTER
    for title, body in sections:
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(72, height - 72, title)
        pdf.setFont("Helvetica", 10)
        text = pdf.beginText(72, height - 100)
        for line in _wrap(body, width - 144):
            text.textLine(line)
        pdf.drawText(text)
        pdf.showPage()
    pdf.save()
    # Round-trip through pypdf to normalise the output (and to confirm parse).
    writer = PdfWriter(clone_from=BytesIO(out.getvalue()))
    final = BytesIO()
    writer.write(final)
    return final.getvalue()


def _wrap(text: str, max_pixels: float) -> list[str]:
    # Crude pixel-budget wrap; demo data only.
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    cur_len = 0
    char_budget = int(max_pixels / 5.5)
    for w in words:
        if cur_len + len(w) + 1 > char_budget:
            lines.append(" ".join(cur))
            cur, cur_len = [], 0
        cur.append(w)
        cur_len += len(w) + 1
    if cur:
        lines.append(" ".join(cur))
    return lines


async def main() -> None:
    Path("scripts/demo_docs").mkdir(parents=True, exist_ok=True)

    for filename, sections in CONTENT.items():
        course_code = filename.split(" ")[0]
        pdf_bytes = make_pdf(sections)
        Path(f"scripts/demo_docs/{filename}").write_bytes(pdf_bytes)

        async with async_session_factory() as session:
            doc = await create_pending_document(
                session,
                title=filename.removesuffix(".pdf"),
                filename=filename,
                course_code=course_code,
                content_hash=hash_bytes(pdf_bytes),
            )
            await session.commit()
            doc_id = doc.id

        await ingest_document(doc_id, pdf_bytes)
        print(f"ingested: {filename}")


if __name__ == "__main__":
    asyncio.run(main())

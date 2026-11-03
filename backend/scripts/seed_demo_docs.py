"""Render the demo course PDFs and ingest them through the real ingestion service.

Run from ``backend/``::

    python -m scripts.seed_demo_docs              # render + ingest (DB from DATABASE_URL)
    python -m scripts.seed_demo_docs --pdf-only   # just write the PDFs (fixtures, demos)

Idempotent: a document whose content hash is already ``ready`` is skipped; an
older version of the same demo file (same filename, different hash) or a
half-ingested copy is deleted and re-ingested. ``--reingest`` forces a fresh
chunk + embed (after changing CHUNK_SIZE_TOKENS or the embedding model).
Exits non-zero if any ingest fails.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from scripts.demo_corpus import Course, load_corpus, render_pdf

DEFAULT_OUT_DIR = Path(__file__).parent / "demo_docs"


def write_pdfs(courses: list[Course], out_dir: Path) -> dict[str, bytes]:
    """Render every course to ``out_dir``; returns {filename: pdf_bytes}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered: dict[str, bytes] = {}
    for course in courses:
        data = render_pdf(course)
        (out_dir / course.filename).write_bytes(data)
        rendered[course.filename] = data
        print(f"wrote   {out_dir / course.filename} ({len(course.sections)} pages)")
    return rendered


async def ingest(
    courses: list[Course], rendered: dict[str, bytes], *, prune: bool, reingest: bool
) -> int:
    # Imported lazily so --pdf-only works without any database configuration.
    from sqlalchemy import delete, select

    from app.db.models import Document, DocumentStatus
    from app.db.session import async_session_factory
    from app.services.ingestion import hash_bytes, ingest_document, register_upload

    failures = 0
    for course in courses:
        pdf_bytes = rendered[course.filename]
        content_hash = hash_bytes(pdf_bytes)

        async with async_session_factory() as session:
            existing = await session.scalar(
                select(Document).where(Document.content_hash == content_hash)
            )
            if existing is not None and existing.status == DocumentStatus.READY and not reingest:
                print(f"skip    {course.filename}: unchanged ({existing.chunk_count} chunks)")
                continue

            # Not ready (or --reingest): any copy with this hash is replaced.
            stale = Document.content_hash == content_hash
            if prune:
                stale = stale | (Document.source_filename == course.filename)
            removed = len(
                (await session.execute(delete(Document).where(stale).returning(Document.id))).all()
            )
            if removed:
                print(f"delete  {course.filename}: {removed} stale or incomplete copy(ies)")

            registration = await register_upload(
                session,
                title=course.title,
                filename=course.filename,
                course_code=course.course_code,
                content_hash=content_hash,
            )
            await session.commit()
            doc_id = registration.document.id

        if registration.should_ingest:
            await ingest_document(doc_id, pdf_bytes)

        async with async_session_factory() as session:
            doc = await session.get(Document, doc_id)
            if doc is None or doc.status != DocumentStatus.READY:
                failures += 1
                reason = doc.error_message if doc is not None else "document vanished"
                print(f"FAILED  {course.filename}: {reason}", file=sys.stderr)
            else:
                print(
                    f"ingest  {course.filename}: {doc.page_count} pages, {doc.chunk_count} chunks"
                )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render and ingest the demo course PDFs.")
    parser.add_argument("--pdf-only", action="store_true", help="write PDFs, skip the database")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--course", action="append", metavar="CODE", help="limit to a course (repeatable)"
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="keep older versions of the demo files instead of replacing them",
    )
    parser.add_argument(
        "--reingest",
        action="store_true",
        help="re-chunk and re-embed even if unchanged (after changing chunking or model)",
    )
    args = parser.parse_args(argv)

    courses = load_corpus()
    if args.course:
        wanted = {c.upper() for c in args.course}
        courses = [c for c in courses if c.course_code in wanted]
        if missing := wanted - {c.course_code for c in courses}:
            parser.error(f"unknown course(s): {', '.join(sorted(missing))}")

    rendered = write_pdfs(courses, args.out_dir)
    if args.pdf_only:
        return 0
    from app.core.config import get_settings
    from app.core.logging import configure_logging

    configure_logging(get_settings().log_level)
    failures = asyncio.run(
        ingest(courses, rendered, prune=not args.no_prune, reingest=args.reingest)
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

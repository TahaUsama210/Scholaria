"""Demo course corpus: Markdown notes in ``scripts/corpus/`` rendered to PDFs.

The Markdown files are the source of truth. Each ``## Heading`` is one section
and is rendered on its own PDF page, so ``(course_code, page)`` identifies a
section; the golden eval dataset labels relevance at that granularity.

Rendering is deterministic (reportlab ``invariant`` mode, fixed metadata), so a
given corpus always produces byte-identical PDFs and therefore identical content
hashes, which is what makes seeding idempotent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

CORPUS_DIR = Path(__file__).parent / "corpus"

# Standard PDF fonts only cover WinAnsi (cp1252); keep the corpus within it so
# text extraction round-trips exactly.
PDF_ENCODING = "cp1252"

_FRONT_MATTER_RE = re.compile(r"\A---\n(?P<meta>.*?)\n---\n", re.DOTALL)


class CorpusError(ValueError):
    """Raised when a corpus file is malformed."""


@dataclass(frozen=True, slots=True)
class Section:
    page: int  # 1-based; one section per PDF page
    title: str
    paragraphs: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True, slots=True)
class Course:
    course_code: str
    course_name: str
    sections: tuple[Section, ...]

    @property
    def title(self) -> str:
        """Document title shown in citations."""
        return f"{self.course_code} {self.course_name}: Course Notes"

    @property
    def filename(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.course_name.lower()).strip("-")
        return f"{self.course_code.lower()}-{slug}.pdf"

    def section(self, page: int) -> Section:
        if not 1 <= page <= len(self.sections):
            raise KeyError(f"{self.course_code} has no page {page}")
        return self.sections[page - 1]


def parse_course(markdown: str, *, source: str = "<string>") -> Course:
    text = markdown.replace("\r\n", "\n")
    match = _FRONT_MATTER_RE.match(text)
    if match is None:
        raise CorpusError(f"{source}: missing '---' front matter")
    meta: dict[str, str] = {}
    for line in match.group("meta").splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            raise CorpusError(f"{source}: bad front matter line {line!r}")
        meta[key.strip()] = value.strip()
    try:
        code, name = meta["course_code"], meta["course_name"]
    except KeyError as exc:
        raise CorpusError(f"{source}: front matter needs {exc.args[0]}") from None

    body = text[match.end() :]
    blocks = re.split(r"^## +", body, flags=re.MULTILINE)
    if blocks[0].strip():
        raise CorpusError(f"{source}: text before the first '## ' heading")

    sections: list[Section] = []
    for page, block in enumerate(blocks[1:], start=1):
        heading, _, rest = block.partition("\n")
        paragraphs = tuple(" ".join(p.split()) for p in re.split(r"\n\s*\n", rest) if p.strip())
        if not paragraphs:
            raise CorpusError(f"{source}: section {heading.strip()!r} is empty")
        sections.append(Section(page=page, title=heading.strip(), paragraphs=paragraphs))
    if not sections:
        raise CorpusError(f"{source}: no sections")
    return Course(course_code=code, course_name=name, sections=tuple(sections))


def load_corpus(directory: Path = CORPUS_DIR) -> list[Course]:
    """All courses in ``directory``, sorted by course code."""
    courses = [
        parse_course(path.read_text(encoding="utf-8"), source=path.name)
        for path in sorted(directory.glob("*.md"))
    ]
    codes = [c.course_code for c in courses]
    if len(set(codes)) != len(codes):
        raise CorpusError(f"duplicate course codes in {directory}: {codes}")
    return sorted(courses, key=lambda c: c.course_code)


# --- PDF rendering --------------------------------------------------------------

_MARGIN = 72.0
_BODY_FONT, _BODY_SIZE, _LEADING = "Helvetica", 10.5, 14.5
_PARAGRAPH_GAP = 6.0


def render_pdf(course: Course) -> bytes:
    """One section per page with a running header and a page-number footer."""
    from reportlab.lib.pagesizes import LETTER  # type: ignore[import-untyped]
    from reportlab.lib.utils import simpleSplit  # type: ignore[import-untyped]
    from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

    width, height = LETTER
    text_width = width - 2 * _MARGIN
    total = len(course.sections)

    out = BytesIO()
    pdf = canvas.Canvas(out, pagesize=LETTER, invariant=1)
    pdf.setTitle(course.title)
    pdf.setAuthor("Scholaria demo corpus")
    pdf.setSubject(f"{course.course_code} course notes")
    pdf.setCreator("scripts/seed_demo_docs.py")

    for section in course.sections:
        pdf.setFont("Helvetica", 8.5)
        pdf.drawString(_MARGIN, height - 48, course.title)
        pdf.drawCentredString(width / 2, 40, f"Page {section.page} of {total}")

        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(_MARGIN, height - 90, f"{section.page}. {section.title}")

        y = height - 118
        for paragraph in section.paragraphs:
            for line in simpleSplit(paragraph, _BODY_FONT, _BODY_SIZE, text_width):
                if y < _MARGIN:
                    raise CorpusError(
                        f"{course.course_code} page {section.page} overflows one page"
                    )
                pdf.setFont(_BODY_FONT, _BODY_SIZE)
                pdf.drawString(_MARGIN, y, line)
                y -= _LEADING
            y -= _PARAGRAPH_GAP
        pdf.showPage()
    pdf.save()
    return out.getvalue()

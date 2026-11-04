"""Shared plumbing for the eval CLIs: thresholds, DB corpus checks, report output."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from eval.dataset import SectionRef
from scripts.demo_corpus import Course, render_pdf

THRESHOLDS_PATH = Path(__file__).parent / "thresholds.json"
DEFAULT_OUT_DIR = Path("eval_results")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def settings_snapshot(settings: object, keys: Sequence[str]) -> dict[str, Any]:
    return {k: getattr(settings, k, None) for k in keys}


# --- thresholds / gating ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThresholdProfile:
    name: str
    match: dict[str, str]
    minimum: dict[str, float]
    maximum: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Check:
    metric: str
    observed: float
    bound: float
    kind: str  # "min" | "max"

    @property
    def passed(self) -> bool:
        if math.isnan(self.observed):
            return False
        return self.observed >= self.bound if self.kind == "min" else self.observed <= self.bound


@dataclass(frozen=True, slots=True)
class GateReport:
    profile: str | None
    checks: tuple[Check, ...]
    note: str = ""

    @property
    def gated(self) -> bool:
        return self.profile is not None

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "passed": self.passed,
            "note": self.note,
            "checks": [
                {
                    "metric": c.metric,
                    "observed": c.observed,
                    c.kind: c.bound,
                    "passed": c.passed,
                }
                for c in self.checks
            ],
        }


def load_profiles(section: str, path: Path = THRESHOLDS_PATH) -> list[ThresholdProfile]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        ThresholdProfile(
            name=p["name"],
            match={k: str(v) for k, v in p["match"].items()},
            minimum={k: float(v) for k, v in p.get("min", {}).items()},
            maximum={k: float(v) for k, v in p.get("max", {}).items()},
        )
        for p in data[section]
    ]


def select_profile(
    profiles: Sequence[ThresholdProfile], settings: object, *, name: str | None = None
) -> ThresholdProfile | None:
    """The named profile, else the first whose ``match`` equals the current settings."""
    if name is not None:
        for p in profiles:
            if p.name == name:
                return p
        raise KeyError(f"no threshold profile named {name!r}")
    for p in profiles:
        if all(str(getattr(settings, k, None)) == v for k, v in p.match.items()):
            return p
    return None


def run_gate(observed: Mapping[str, float], profile: ThresholdProfile | None) -> GateReport:
    if profile is None:
        return GateReport(
            profile=None,
            checks=(),
            note="no threshold profile matches these settings; results are not gated",
        )
    checks = [
        Check(m, observed.get(m, float("nan")), bound, "min")
        for m, bound in profile.minimum.items()
    ] + [
        Check(m, observed.get(m, float("nan")), bound, "max")
        for m, bound in profile.maximum.items()
    ]
    return GateReport(profile=profile.name, checks=tuple(checks))


def gate_lines(gate: GateReport) -> list[str]:
    if not gate.gated:
        return [f"Gate: not applied ({gate.note})."]
    lines = [f"Gate profile `{gate.profile}`: {'PASS' if gate.passed else 'FAIL'}", ""]
    rows = [
        [
            c.metric,
            fmt(c.observed),
            f"{'>=' if c.kind == 'min' else '<='} {c.bound:.3f}",
            "pass" if c.passed else "FAIL",
        ]
        for c in gate.checks
    ]
    return lines + md_table(["metric", "observed", "threshold", "result"], rows)


# --- corpus in the database -------------------------------------------------------


class CorpusNotSeededError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CorpusIndex:
    doc_course: dict[UUID, str]  # ready demo documents -> course code
    chunk_count: int
    extra_documents: int  # ready documents that are not part of the demo corpus
    chunk_tokens_mean: float = 0.0  # as ingested, which may differ from current settings
    chunk_tokens_max: int = 0

    def section(self, document_id: UUID, page: int | None) -> SectionRef:
        # Chunks of non-corpus documents get a unique, never-relevant ref.
        course = self.doc_course.get(document_id, f"other:{document_id}")
        return SectionRef(course, page or 0)


async def load_corpus_index(session: AsyncSession, corpus: Sequence[Course]) -> CorpusIndex:
    """Verify every course is ingested from the *current* corpus (by content hash)."""
    expected = {sha256(render_pdf(c)).hexdigest(): c.course_code for c in corpus}
    rows = (
        await session.execute(text("SELECT id, content_hash, status, chunk_count FROM documents"))
    ).all()
    doc_course: dict[UUID, str] = {}
    chunks = extra = 0
    for row in rows:
        code = expected.get(row.content_hash)
        if code is not None and row.status == "ready":
            doc_course[row.id] = code
            chunks += row.chunk_count
        elif code is None and row.status == "ready":
            extra += 1
    missing = sorted(set(expected.values()) - set(doc_course.values()))
    if missing:
        raise CorpusNotSeededError(
            f"demo corpus not ingested (or stale) for {', '.join(missing)}; "
            "run `python -m scripts.seed_demo_docs` against this database first"
        )
    stats = (
        await session.execute(
            text(
                "SELECT coalesce(avg(token_count), 0) AS mean, coalesce(max(token_count), 0) AS max"
                " FROM chunks WHERE document_id = ANY(:ids)"
            ),
            {"ids": list(doc_course)},
        )
    ).one()
    return CorpusIndex(
        doc_course=doc_course,
        chunk_count=chunks,
        extra_documents=extra,
        chunk_tokens_mean=float(stats.mean),
        chunk_tokens_max=int(stats.max),
    )


# --- output ---------------------------------------------------------------------


def provider_label(provider: object, model: object) -> str:
    """``provider:model``, or just the provider when it has no model (hash, none, fake)."""
    return str(provider) if provider in ("hash", "none", "fake") else f"{provider}:{model}"


def fmt(value: float | None, digits: int = 3) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value:.{digits}f}"


def md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _json_default(obj: object) -> object:
    if isinstance(obj, UUID | SectionRef | Path):
        return str(obj)
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def write_report(out_dir: Path, stem: str, payload: Mapping[str, Any], markdown: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.json").write_text(
        json.dumps(payload, indent=2, default=_json_default, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    md_path = out_dir / f"{stem}.md"
    md_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return md_path


def clean_floats(obj: Any) -> Any:
    """NaN/inf -> None so reports stay strict JSON."""
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else round(obj, 4)
    if isinstance(obj, dict):
        return {k: clean_floats(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [clean_floats(v) for v in obj]
    return obj

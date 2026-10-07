"""Checks behind `reviewtrace guard`."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from reviewtrace.extractors.docx import load_document
from reviewtrace.models.document import Document
from reviewtrace.utils.errors import ReviewTraceError
from reviewtrace.utils.files import sha256_of

HEDGES = (
    "may",
    "might",
    "could",
    "suggests",
    "suggest",
    "appears",
    "potentially",
    "possibly",
    "likely",
    "non-significant",
    "not significant",
)
# 12, 1,000, 12.5, 12.50%, -3, and values with a unit-free percent sign.
NUMBER = re.compile(r"(?<![\w.])[+-]?\d[\d,]*(?:\.\d+)?\s?%?")


class GuardStatus(str, Enum):
    PROTECTED_INTACT = "PROTECTED_INTACT"
    PROTECTED_CHANGED = "PROTECTED_CHANGED"
    PROTECTED_REMOVED = "PROTECTED_REMOVED"
    INVALID_PROTECTION = "INVALID_PROTECTION"
    NUMBERS_CHANGED = "NUMBERS_CHANGED"
    HEDGE_REMOVED = "HEDGE_REMOVED"
    TABLE_CHANGED = "TABLE_CHANGED"
    UNCHANGED = "UNCHANGED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class GuardFinding(BaseModel):
    status: GuardStatus
    message: str
    evidence: dict[str, object] = Field(default_factory=dict)


class GuardReport(BaseModel):
    original: str
    revised: str
    original_sha256: str
    revised_sha256: str
    findings: list[GuardFinding]
    scope: str = (
        "Extracted body, table and footnote text only. No formatting comparison and no "
        "judgement of whether a change is correct."
    )

    @property
    def flagged(self) -> bool:
        quiet = {
            GuardStatus.PROTECTED_INTACT,
            GuardStatus.UNCHANGED,
            GuardStatus.NEEDS_REVIEW,
        }
        return any(f.status not in quiet for f in self.findings)


def _norm_number(raw: str) -> str:
    """12.50% and 12.5 % compare equal; 1,000 and 1000 compare equal."""
    text = raw.replace(",", "").replace(" ", "")
    percent = text.endswith("%")
    text = text.rstrip("%")
    try:
        value = float(text)
    except ValueError:
        return raw.strip()
    out = f"{value:g}"
    return out + "%" if percent else out


def _text(doc: Document) -> str:
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        parts.extend(" | ".join(row) for row in table.rows)
    parts.extend(note.text for note in doc.footnotes)
    return "\n".join(parts)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def guard_revision(
    original: str | Path,
    revised: str | Path,
    protected: Sequence[str] = (),
) -> GuardReport:
    """Compare two DOCX files for unrequested changes. Never modifies either file."""
    o_path, r_path = Path(original), Path(revised)
    o_doc = load_document(o_path, comments=False)
    r_doc = load_document(r_path, comments=False)
    before, after = _text(o_doc), _text(r_doc)
    found: list[GuardFinding] = []

    flat_before, flat_after = _squash(before), _squash(after)
    for phrase in protected:
        if not isinstance(phrase, str) or not phrase.strip():
            raise ReviewTraceError("protected phrases must be non-empty strings")
        target = _squash(phrase)
        n_before, n_after = flat_before.count(target), flat_after.count(target)
        evidence: dict[str, object] = {"text": phrase, "before": n_before, "after": n_after}
        if n_before == 0:
            found.append(
                GuardFinding(
                    status=GuardStatus.INVALID_PROTECTION,
                    message="The protected text is not in the original, so nothing was checked.",
                    evidence=evidence,
                )
            )
        elif n_after == 0:
            found.append(
                GuardFinding(
                    status=GuardStatus.PROTECTED_REMOVED,
                    message="Protected text no longer appears in the revision.",
                    evidence=evidence,
                )
            )
        elif n_after != n_before:
            found.append(
                GuardFinding(
                    status=GuardStatus.PROTECTED_CHANGED,
                    message="Protected text appears a different number of times.",
                    evidence=evidence,
                )
            )
        else:
            found.append(
                GuardFinding(
                    status=GuardStatus.PROTECTED_INTACT,
                    message="Protected text is present the same number of times.",
                    evidence=evidence,
                )
            )

    nb = Counter(_norm_number(m.group()) for m in NUMBER.finditer(before))
    na = Counter(_norm_number(m.group()) for m in NUMBER.finditer(after))
    removed, added = sorted((nb - na).elements()), sorted((na - nb).elements())
    if removed or added:
        found.append(
            GuardFinding(
                status=GuardStatus.NUMBERS_CHANGED,
                message="Numbers differ between the versions; check each against its source.",
                evidence={"removed": removed, "added": added},
            )
        )

    for word in HEDGES:
        pattern = re.compile(rf"\b{re.escape(word)}\b", re.I)
        b, a = len(pattern.findall(before)), len(pattern.findall(after))
        if b > a:
            found.append(
                GuardFinding(
                    status=GuardStatus.HEDGE_REMOVED,
                    message=f"'{word}' appears less often; a claim may have been strengthened.",
                    evidence={"word": word, "before": b, "after": a},
                )
            )

    if [t.rows for t in o_doc.tables] != [t.rows for t in r_doc.tables]:
        found.append(
            GuardFinding(
                status=GuardStatus.TABLE_CHANGED,
                message="Table contents differ; inspect cell values.",
                evidence={"tables_before": len(o_doc.tables), "tables_after": len(r_doc.tables)},
            )
        )
    if before == after:
        found.append(
            GuardFinding(status=GuardStatus.UNCHANGED, message="Extracted text is identical.")
        )
    elif not any(f.status is not GuardStatus.PROTECTED_INTACT for f in found):
        found.append(
            GuardFinding(
                status=GuardStatus.NEEDS_REVIEW,
                message="Wording changed, but no protected text, number or hedge was affected.",
            )
        )
    return GuardReport(
        original=str(o_path),
        revised=str(r_path),
        original_sha256=sha256_of(o_path)[0],
        revised_sha256=sha256_of(r_path)[0],
        findings=found,
    )

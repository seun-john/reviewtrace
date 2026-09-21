"""Turn a separate reviewer report (DOCX, Markdown or text) into review issues.

Numbered, bulleted, labelled ("Comment 3:") and tabular items each become one issue.
When a report has no list structure, the requests inside its prose are extracted
sentence by sentence; sentences that are not requests are skipped and counted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from reviewtrace.analysis.classifier import looks_like_request
from reviewtrace.extractors.docx import load_document
from reviewtrace.extractors.issues import build_issue, issue_id, mark_duplicates
from reviewtrace.extractors.markdown import (
    LABELLED,
    Block,
    clean_inline,
    parse_blocks,
    read_text_file,
    table_rows_to_blocks,
)
from reviewtrace.models.document import Location
from reviewtrace.models.issue import IssueSource, ReviewIssue, Severity
from reviewtrace.utils.errors import UnsupportedFileTypeError
from reviewtrace.utils.text import sentences

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text"}
_TEXT_LABEL = re.compile(r"^\s*(?:(\d{1,3})[.)]|\((\d{1,3})\))\s+(\S.*)$")
_SEV_HEADING = re.compile(r"\b(major|critical|essential|serious|significant)\b", re.I)
_MIN_HEADING = re.compile(r"\b(minor|typographical|editorial|typos?|suggestions?|optional)\b", re.I)
_REVIEWER_HEADING = re.compile(
    r"^(?:comments?\s+(?:from|by)\s+)?(reviewer\s*(?:#|no\.?)?\s*\w{1,3}|referee\s*(?:#)?\s*\w{1,3}|"
    r"supervisor|editor|examiner|client|reader)\b",
    re.I,
)


@dataclass
class ReportExtraction:
    issues: list[ReviewIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ignored_sentences: int = 0


def _docx_blocks(path: Path) -> list[Block]:
    doc = load_document(path, comments=False, numbered_heading_fallback=False)
    entries: list[tuple[float, Block]] = []
    for p in doc.paragraphs:
        if p.is_heading:
            entries.append((p.number, Block("heading", p.text, level=p.heading_level)))
            continue
        m = _TEXT_LABEL.match(p.text) or None
        lab = LABELLED.match(p.text)
        if m:
            entries.append(
                (p.number, Block("item", clean_inline(m.group(3)), label=m.group(1) or m.group(2)))
            )
        elif lab:
            entries.append(
                (p.number, Block("item", clean_inline(lab.group(2)), label=lab.group(1)))
            )
        elif p.is_list_item:
            entries.append((p.number, Block("item", p.text)))
        else:
            entries.append((p.number, Block("paragraph", p.text)))
    for t in doc.tables:
        if t.rows:
            for b in table_rows_to_blocks(t.rows[0], t.rows[1:]):
                entries.append((t.after_paragraph + 0.5, b))
    entries.sort(key=lambda e: e[0])
    return [b for _, b in entries]


def read_blocks(path: str | Path) -> list[Block]:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".docx":
        return _docx_blocks(p)
    if suffix in TEXT_SUFFIXES:
        return parse_blocks(read_text_file(str(p)))
    raise UnsupportedFileTypeError(
        f"unsupported review-report type '{p.suffix or '(none)'}' for {p.name}; use .docx, .md or .txt"
    )


def extract_report(path: str | Path) -> ReportExtraction:
    blocks = read_blocks(path)
    result = ReportExtraction()
    items = [b for b in blocks if b.kind == "item"]
    severity: Severity | None = None
    reviewer = ""
    counter = 0

    def emit(text: str, label: str | None, extra: str = "") -> None:
        nonlocal counter
        if not text.strip():
            return
        counter += 1
        loc = Location(section=extra) if extra else None
        result.issues.append(
            build_issue(
                issue_id(counter),
                IssueSource.REVIEW_REPORT,
                text,
                source_id=label,
                reviewer=reviewer,
                anchor_location=loc,
                severity_hint=severity,
                infer_anchor=True,
            )
        )

    list_mode = len(items) >= 2
    for b in blocks:
        if b.kind == "heading":
            if _MIN_HEADING.search(b.text):
                severity = Severity.MINOR
            elif _SEV_HEADING.search(b.text):
                severity = Severity.MAJOR
            m = _REVIEWER_HEADING.match(b.text.strip())
            if m:
                reviewer = clean_inline(m.group(1)).title()
                severity = None
            continue
        if b.kind == "item":
            emit(b.text, b.label, b.extra)
        elif not list_mode and b.kind == "paragraph":
            for s in sentences(b.text):
                if looks_like_request(s):
                    emit(s, None)
                else:
                    result.ignored_sentences += 1
        else:
            result.ignored_sentences += len(sentences(b.text))
    if not result.issues:
        result.warnings.append("no review items were found in the report")
    elif result.ignored_sentences:
        result.warnings.append(
            f"{result.ignored_sentences} sentence(s) that do not read as requests were not extracted as issues"
        )
    mark_duplicates(result.issues)
    return result

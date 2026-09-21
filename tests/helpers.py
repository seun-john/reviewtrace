"""Shared helpers for building document pairs and running one-off assessments."""

from __future__ import annotations

from pathlib import Path

from reviewtrace.core import run_audit
from reviewtrace.extractors.docx import load_document
from reviewtrace.extractors.issues import build_issue, issue_id
from reviewtrace.models.document import Location
from reviewtrace.models.finding import Finding
from reviewtrace.models.issue import IssueSource
from reviewtrace.models.report import AuditMode, AuditReport
from tests.fixtures.docx_builder import DocxBuilder


def make_issue(n: int, comment: str, anchor: str = "", section: str | None = None, **kw):  # type: ignore[no-untyped-def]
    loc = Location(section=section) if section else None
    return build_issue(
        issue_id(n),
        IssueSource.ISSUES_FILE,
        comment,
        anchor_text=anchor,
        anchor_location=loc,
        **kw,
    )


def audit_pair(
    tmp_path: Path,
    original: DocxBuilder,
    revised: DocxBuilder,
    *issues,  # type: ignore[no-untyped-def]
) -> AuditReport:
    o = original.save(tmp_path / "o.docx")
    r = revised.save(tmp_path / "r.docx")
    return run_audit(
        AuditMode.ISSUES_FILE,
        load_document(o, comments=False),
        load_document(r, comments=False),
        list(issues),
        [],
    )


def one(
    tmp_path: Path,
    original: DocxBuilder,
    revised: DocxBuilder,
    comment: str,
    anchor: str = "",
    section: str | None = None,
    **kw,  # type: ignore[no-untyped-def]
) -> Finding:
    report = audit_pair(tmp_path, original, revised, make_issue(1, comment, anchor, section, **kw))
    return report.findings[0]


def base_doc(*paragraphs: str) -> DocxBuilder:
    """A small thesis-like document: one section per (heading, paragraphs) pair is overkill,
    so tests pass explicit content; this builds a single numbered section."""
    b = DocxBuilder()
    b.heading("1 Introduction", 1)
    for p in paragraphs:
        b.para(p)
    return b

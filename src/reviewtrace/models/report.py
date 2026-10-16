"""AuditReport: the complete, serialisable result of one audit."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field

from reviewtrace import __version__
from reviewtrace.models.diff import DiffSummary
from reviewtrace.models.finding import STATUS_ORDER, Confidence, Finding, Status
from reviewtrace.models.issue import ReviewIssue

SCHEMA_VERSION = 1
DECIDED = (Status.RESOLVED, Status.PARTIALLY_RESOLVED, Status.UNRESOLVED)


def describe_coverage(block: dict[str, Any]) -> str:
    """One line for people: `decided 5 of 20 comments; 15 could not be checked (...)`."""
    text = f"decided {block['decided']} of {block['items']} {block['unit']}"
    if block["undecided"]:
        why = ", ".join(
            f"{k} {v}" for k, v in sorted(block["reasons"].items(), key=lambda kv: -kv[1])
        )
        text += f"; {block['undecided']} could not be checked ({why})"
    return text


def decided_share(block: dict[str, Any]) -> float | None:
    """Percent of items decided, or None when there were no items."""
    return round(100 * block["decided"] / block["items"], 1) if block["items"] else None


class AuditMode(str, Enum):
    WORD_COMMENTS = "word_comments"
    REVIEW_REPORT = "review_report"
    ISSUES_FILE = "issues_file"


class InputFile(BaseModel):
    role: str
    path: str
    sha256: str
    size_bytes: int


class AuditReport(BaseModel):
    schema_version: int = SCHEMA_VERSION
    reviewtrace_version: str = __version__
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    mode: AuditMode
    inputs: list[InputFile] = Field(default_factory=list)
    semantic_reviewer: str = "disabled"
    issues: list[ReviewIssue] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    diff_summary: DiffSummary = Field(default_factory=DiffSummary)
    warnings: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def coverage(self) -> dict[str, Any]:
        """The shared coverage block: how many comments got a verdict, and why not the rest.

        A comment is decided when it came out RESOLVED, PARTIALLY_RESOLVED or UNRESOLVED.
        NEEDS_REVIEW, NOT_ASSESSABLE and ERROR are undecided. A NOT_ASSESSABLE comment is
        grouped by its category (``not_assessable_explain``) so it is clear what kinds of
        request cannot be judged.
        """
        reasons: dict[str, int] = {}
        decided = 0
        for f in self.findings:
            if f.status in DECIDED:
                decided += 1
                continue
            if f.status is Status.NOT_ASSESSABLE:
                issue = self.issue_for(f.issue_id)
                reason = (
                    f"not_assessable_{issue.category.value.lower()}" if issue else "not_assessable"
                )
            else:
                reason = f.status.value.lower()
            reasons[reason] = reasons.get(reason, 0) + 1
        return {
            "unit": "comments",
            "items": len(self.findings),
            "decided": decided,
            "undecided": len(self.findings) - decided,
            "reasons": dict(sorted(reasons.items())),
            "suppressed": 0,
            "notes": [],
        }

    def finding_for(self, issue_id: str) -> Finding | None:
        for f in self.findings:
            if f.issue_id == issue_id:
                return f
        return None

    def issue_for(self, issue_id: str) -> ReviewIssue | None:
        for i in self.issues:
            if i.id == issue_id:
                return i
        return None

    def status_counts(self) -> dict[Status, int]:
        counts = dict.fromkeys(STATUS_ORDER, 0)
        for f in self.findings:
            counts[f.status] += 1
        return counts

    def confidence_counts(self) -> dict[Confidence, int]:
        counts = dict.fromkeys(Confidence, 0)
        for f in self.findings:
            counts[f.confidence] += 1
        return counts

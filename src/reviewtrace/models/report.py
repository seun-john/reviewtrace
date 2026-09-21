"""AuditReport: the complete, serialisable result of one audit."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from reviewtrace import __version__
from reviewtrace.models.diff import DiffSummary
from reviewtrace.models.finding import STATUS_ORDER, Confidence, Finding, Status
from reviewtrace.models.issue import ReviewIssue

SCHEMA_VERSION = 1


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

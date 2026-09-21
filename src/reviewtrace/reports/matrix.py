"""The traceability matrix: one row per issue (and per sub-requirement of compound issues)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from reviewtrace.models.evidence import EvidenceDirection
from reviewtrace.models.finding import Finding
from reviewtrace.models.issue import ReviewIssue
from reviewtrace.models.report import AuditReport
from reviewtrace.utils.text import truncate

MARKS = {
    EvidenceDirection.SUPPORTS: "+",
    EvidenceDirection.CONTRADICTS: "-",
    EvidenceDirection.CONTEXT: "·",
}

# The ten columns of the formal matrix, then the fields that keep the three concepts apart.
CORE_COLUMNS = [
    ("ID", "id"),
    ("Reviewer", "reviewer"),
    ("Comment", "comment"),
    ("Anchored Context", "anchored_context"),
    ("Requested Action", "requested_action"),
    ("Status", "status"),
    ("Confidence", "confidence"),
    ("Evidence", "evidence"),
    ("Revised Location", "revised_location"),
    ("Remaining Issue", "remaining_issue"),
]
EXTRA_COLUMNS = [
    ("Reviewer Marked Resolved", "reviewer_marked_resolved"),
    ("Document Changed", "document_changed"),
    ("Assessment Source", "assessment_source"),
    ("Method", "method"),
    ("Needs Human Review", "needs_human_review"),
]


@dataclass
class MatrixRow:
    id: str
    reviewer: str
    comment: str
    anchored_context: str
    requested_action: str
    status: str
    confidence: str
    evidence: str
    revised_location: str
    remaining_issue: str
    reviewer_marked_resolved: str
    document_changed: str
    assessment_source: str
    method: str
    needs_human_review: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _yes_no(value: bool | None, unknown: str) -> str:
    return unknown if value is None else ("yes" if value else "no")


def _anchor_cell(issue: ReviewIssue) -> str:
    where = issue.anchor_location.label() if issue.anchor_location else ""
    where = "" if where == "unlocated" else where
    if issue.anchor_text:
        quoted = f'"{truncate(issue.anchor_text, 200)}"'
        return f"{quoted} ({where})" if where else quoted
    return where


def _evidence_cell(f: Finding, limit: int = 4) -> str:
    items = [e for e in f.evidence if e.direction is not EvidenceDirection.CONTEXT] or f.evidence
    lines = [f"{MARKS[e.direction]} {e.explanation}" for e in items[:limit]]
    if len(items) > limit:
        lines.append(f"(+{len(items) - limit} more)")
    return "\n".join(lines)


def _location_cell(f: Finding, limit: int = 3) -> str:
    labels: list[str] = []
    for loc in f.revised_locations:
        label = loc.label()
        if label != "unlocated" and label not in labels:
            labels.append(label)
    return "; ".join(labels[:limit])


def _remaining(f: Finding) -> str:
    if f.remaining_action:
        return f.remaining_action
    return "; ".join(f.missing_elements)


def _row(issue: ReviewIssue, f: Finding, *, id_: str, comment: str, action: str) -> MatrixRow:
    return MatrixRow(
        id=id_,
        reviewer=issue.reviewer,
        comment=comment,
        anchored_context=_anchor_cell(issue),
        requested_action=action,
        status=f.status.label,
        confidence=f.confidence.value,
        evidence=_evidence_cell(f),
        revised_location=_location_cell(f),
        remaining_issue=_remaining(f),
        reviewer_marked_resolved=_yes_no(f.word_comment_resolved, "n/a"),
        document_changed=_yes_no(f.document_changed, "unknown"),
        assessment_source=f.assessment_source.value,
        method=f.method,
        needs_human_review=_yes_no(f.requires_human_review, "unknown"),
    )


def build_matrix(report: AuditReport) -> list[MatrixRow]:
    rows: list[MatrixRow] = []
    for issue in report.issues:
        f = report.finding_for(issue.id)
        if f is None:
            continue
        action = (
            f"{issue.category.value}: {issue.requested_action}"
            if issue.requested_action
            else issue.category.value
        )
        rows.append(_row(issue, f, id_=issue.id, comment=issue.comment_text, action=action))
        if f.sub_findings and len(f.sub_findings) == len(issue.sub_requirements):
            for sub, sf in zip(issue.sub_requirements, f.sub_findings, strict=True):
                rows.append(
                    _row(
                        issue,
                        sf,
                        id_=sub.id,
                        comment=sub.text,
                        action=f"{sub.category.value}: {sub.text}",
                    )
                )
    return rows

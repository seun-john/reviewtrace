"""Draft a response-to-reviewers letter from an audit.

Every sentence is taken from a finding: nothing is invented. Only RESOLVED findings are
answered "Addressed."; anything else says further revision (or author confirmation) is
still required and names what remains.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reviewtrace.models.evidence import Evidence, EvidenceDirection, EvidenceType
from reviewtrace.models.finding import Finding, Status
from reviewtrace.models.issue import ReviewIssue
from reviewtrace.models.report import AuditReport
from reviewtrace.reports.markdown import md_escape
from reviewtrace.utils.text import truncate


@dataclass
class ResponseDraft:
    text: str
    caveats: list[str] = field(
        default_factory=list
    )  # issue ids whose response needs author checking


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _changes(f: Finding, excerpts: bool) -> list[str]:
    lines: list[str] = []
    for e in f.evidence:
        if e.direction is not EvidenceDirection.SUPPORTS or e.type is EvidenceType.TRACKED_REVISION:
            continue
        line = _sentence(md_escape(e.explanation))
        if (
            excerpts
            and e.revised_text
            and e.type in (EvidenceType.TEXT_ADDED, EvidenceType.TEXT_MODIFIED)
        ):
            line += f" Added text: “{md_escape(truncate(e.revised_text, 200))}”"
        if line not in lines:
            lines.append(line)
    return lines


def _locations(f: Finding) -> str:
    labels: list[str] = []
    for e in f.evidence:
        if e.direction is EvidenceDirection.SUPPORTS and e.revised_location is not None:
            label = e.revised_location.label()
            if label != "unlocated" and label not in labels:
                labels.append(label)
    return "; ".join(labels[:3])


def _detected(f: Finding) -> list[Evidence]:
    return [
        e
        for e in f.evidence
        if e.direction is EvidenceDirection.CONTEXT and e.type is not EvidenceType.TRACKED_REVISION
    ]


def _block(n: int, issue: ReviewIssue, f: Finding, excerpts: bool) -> list[str]:
    lines = [
        f"### Comment {n}",
        "",
        f"<!-- {issue.id} -->",
        md_escape(issue.comment_text),
        "",
        "**Response:**",
        "",
    ]
    changes = _changes(f, excerpts)
    where = _locations(f)
    if f.status is Status.RESOLVED:
        lines += ["Addressed.", ""]
        if changes:
            lines += ["**Changes made:**", "", *changes, ""]
        if where:
            lines += ["**Location:**", "", md_escape(where) + ".", ""]
    elif f.status is Status.PARTIALLY_RESOLVED:
        lines += ["Partially addressed. Further revision required.", ""]
        if changes:
            lines += ["**Changes made so far:**", "", *changes, ""]
        if where:
            lines += ["**Location:**", "", md_escape(where) + ".", ""]
        outstanding = f.missing_elements or ([f.remaining_action] if f.remaining_action else [])
        if outstanding:
            lines += (
                ["**Still outstanding:**", ""] + [f"- {md_escape(m)}" for m in outstanding] + [""]
            )
    elif f.status is Status.UNRESOLVED:
        lines += ["Further revision required.", ""]
        if f.remaining_action:
            lines += ["**Not yet done:**", "", _sentence(md_escape(f.remaining_action)), ""]
    else:
        lines += [
            "Further revision required: the author must confirm how this comment is addressed.",
            "",
        ]
        detected = [_sentence(md_escape(e.explanation)) for e in _detected(f)[:3]]
        if detected:
            lines += [
                "**Related changes detected (not confirmed as addressing the comment):**",
                "",
                *detected,
                "",
            ]
        if f.status is Status.NOT_ASSESSABLE:
            lines += [f"**Note:** {_sentence(md_escape(f.rationale))}", ""]
    if f.sub_findings and len(f.sub_findings) == len(issue.sub_requirements):
        lines += ["**By requirement:**", ""]
        for sub, sf in zip(issue.sub_requirements, f.sub_findings, strict=True):
            state = "addressed" if sf.status is Status.RESOLVED else "further revision required"
            lines.append(f"- {md_escape(sub.text.rstrip('.'))}: {state}")
        lines.append("")
    return lines


def generate_response(report: AuditReport, *, excerpts: bool = False) -> ResponseDraft:
    lines: list[str] = ["# Response to reviewers", ""]
    caveats: list[str] = []
    reviewers = [i.reviewer for i in report.issues if i.reviewer]
    grouped = len(set(reviewers)) > 1
    current: str | None = None
    for n, issue in enumerate(report.issues, start=1):
        f = report.finding_for(issue.id)
        if f is None:
            continue
        if grouped and issue.reviewer != current:
            current = issue.reviewer
            lines += [f"## {md_escape(current or 'Reviewer')}", ""]
        lines += _block(n, issue, f, excerpts)
        if f.status is not Status.RESOLVED or f.requires_human_review:
            caveats.append(issue.id)
    return ResponseDraft(text="\n".join(lines).rstrip() + "\n", caveats=caveats)

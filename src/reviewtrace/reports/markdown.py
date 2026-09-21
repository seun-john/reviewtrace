"""Markdown audit report: summary, traceability matrix and per-issue evidence.

Comment and document text is untrusted. ``md_escape`` neutralises links, images, raw HTML
and table breaks so that rendering a report can never fetch a URL or inject markup.
"""

from __future__ import annotations

import re

from reviewtrace.models.evidence import EvidenceDirection
from reviewtrace.models.finding import STATUS_ORDER
from reviewtrace.models.report import AuditReport
from reviewtrace.reports.matrix import CORE_COLUMNS, MARKS, build_matrix
from reviewtrace.utils.text import truncate

_WS = re.compile(r"[ \t]+")


def md_escape(text: str) -> str:
    out = text.replace("\\", "\\\\")
    for ch, rep in (
        ("<", "&lt;"),
        (">", "&gt;"),
        ("[", "\\["),
        ("]", "\\]"),
        ("|", "\\|"),
        ("`", "\\`"),
    ):
        out = out.replace(ch, rep)
    return _WS.sub(" ", out)


def _cell(text: str, limit: int = 220) -> str:
    return md_escape(truncate(text, limit)).replace("\n", "<br>")


def _quote(text: str) -> str:
    return "\n".join(
        f"> {md_escape(line)}" if line.strip() else ">" for line in text.splitlines() or [""]
    )


def _tri_state(value: bool | None, yes: str, no: str, unknown: str) -> str:
    return unknown if value is None else (yes if value else no)


def _finding_section(report: AuditReport, issue_id: str) -> list[str]:
    issue, f = report.issue_for(issue_id), report.finding_for(issue_id)
    if issue is None or f is None:
        return []
    lines = [f"### {issue.id} — {f.status.label} (confidence: {f.confidence.value})", ""]
    if issue.reviewer or issue.date:
        lines.append(
            f"**Reviewer:** {md_escape(issue.reviewer or 'unknown')}"
            + (f" · {md_escape(issue.date)}" if issue.date else "")
        )
        lines.append("")
    lines += ["**Comment**", "", _quote(issue.comment_text), ""]
    if issue.anchor_text:
        lines += ["**Anchored text**", "", _quote(truncate(issue.anchor_text, 600)), ""]
    where = issue.anchor_location.label()
    if where != "unlocated":
        lines += [f"**Original location:** {md_escape(where)}", ""]
    if f.located_by:
        lines += [f"**Located by:** {md_escape(f.located_by)}", ""]
    if issue.requested_action:
        lines += [
            f"**Requested action:** {issue.category.value} — {md_escape(issue.requested_action)}",
            "",
        ]
    lines += [
        "| Reviewer's tool | Document | ReviewTrace assessment |",
        "| --- | --- | --- |",
        "| {} | {} | {} ({}, {}) |".format(
            _tri_state(
                f.word_comment_resolved,
                "marked resolved",
                "not marked resolved",
                "no resolution metadata",
            ),
            _tri_state(
                f.document_changed,
                "changed at the target",
                "unchanged at the target",
                "target unknown",
            ),
            f.status.label,
            f.confidence.value,
            f.assessment_source.value,
        ),
        "",
    ]
    if f.evidence:
        lines += ["**Evidence**", ""]
        for e in f.evidence:
            lines.append(f"- `{MARKS[e.direction]}` {md_escape(e.explanation)}")
            if e.revised_location and e.revised_location.label() != "unlocated":
                lines.append(f"  - revised location: {md_escape(e.revised_location.label())}")
            if e.direction is EvidenceDirection.SUPPORTS and e.revised_text:
                lines.append(f"  - new text: “{md_escape(truncate(e.revised_text, 240))}”")
        lines.append("")
    if f.missing_elements:
        lines += ["**Missing**", ""] + [f"- {md_escape(m)}" for m in f.missing_elements] + [""]
    if f.remaining_action:
        lines += [f"**Remaining action:** {md_escape(f.remaining_action)}", ""]
    lines += [f"**Rationale:** {md_escape(f.rationale)}", ""]
    if f.requires_human_review:
        lines += ["_Human review recommended for this finding._", ""]
    if f.sub_findings and len(f.sub_findings) == len(issue.sub_requirements):
        lines += ["**Requirements**", ""]
        for sub, sf in zip(issue.sub_requirements, f.sub_findings, strict=True):
            lines.append(
                f"- {sub.id} {md_escape(sub.text)} — {sf.status.label} ({sf.confidence.value})"
            )
        lines.append("")
    if issue.possible_duplicates:
        lines += [
            f"_Possible duplicate of: {', '.join(issue.possible_duplicates)} (not merged)._",
            "",
        ]
    return lines


def render_matrix_markdown(report: AuditReport) -> str:
    headers = [title for title, _ in CORE_COLUMNS]
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in build_matrix(report):
        data = row.as_dict()
        out.append("| " + " | ".join(_cell(data[key]) for _, key in CORE_COLUMNS) + " |")
    return "\n".join(out) + "\n"


def _summary_table(report: AuditReport) -> list[str]:
    counts = report.status_counts()
    lines = ["| Status | Issues |", "| --- | ---: |"]
    lines += [f"| {s.label.title()} | {counts[s]} |" for s in STATUS_ORDER]
    lines.append(f"| **Total** | **{len(report.findings)}** |")
    return lines


def render_markdown(report: AuditReport) -> str:
    d = report.diff_summary
    lines = [
        "# ReviewTrace audit report",
        "",
        f"- Workflow: {report.mode.value.replace('_', ' ')}",
        f"- Generated: {report.generated_at} by ReviewTrace {report.reviewtrace_version}",
        f"- Semantic assessment: {report.semantic_reviewer}",
        "",
        "| Input | File | SHA-256 |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| {md_escape(i.role)} | {md_escape(i.path)} | `{i.sha256[:16]}…` |" for i in report.inputs
    ]
    lines += ["", "## Summary", "", *_summary_table(report)]
    conf = report.confidence_counts()
    lines += [
        "",
        "Confidence: " + ", ".join(f"{c.value.lower()} {n}" for c, n in conf.items()) + ".",
        "",
        f"Document changes: {d.paragraphs_added} paragraphs added, {d.paragraphs_removed} removed, "
        f"{d.paragraphs_modified} modified, {d.paragraphs_moved} moved; {d.sections_added} sections added, "
        f"{d.sections_removed} removed, {d.sections_renamed} renamed; {d.tables_modified} tables modified; "
        f"{d.references_added} references added; {d.tracked_insertions} tracked insertions, "
        f"{d.tracked_deletions} tracked deletions in the revised file.",
        "",
        "## Traceability matrix",
        "",
        render_matrix_markdown(report),
        "## Findings",
        "",
    ]
    for issue in report.issues:
        lines += _finding_section(report, issue.id)
    if report.warnings:
        lines += ["## Notes", ""] + [f"- {md_escape(w)}" for w in report.warnings] + [""]
    lines += [
        "## Status definitions",
        "",
        "- **Resolved**: supporting evidence shows the requested change was made.",
        "- **Partially resolved**: part of the request was met and an identifiable requirement remains.",
        "- **Unresolved**: evidence shows the change was not made or is clearly absent.",
        "- **Needs review**: changes exist but adequacy cannot be judged automatically.",
        "- **Not assessable**: the evidence needed is not available.",
        "- **Error**: a technical problem prevented assessment.",
        "",
    ]
    return "\n".join(lines)

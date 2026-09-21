"""Rich terminal rendering.

All document and comment text is untrusted, so it is always placed in ``Text`` objects
(never interpreted as Rich markup) and control characters are stripped by Rich.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.text import Text as RichText

from reviewtrace.models.diff import ChangeKind, DocumentDiff, SectionMatchKind, TableChangeKind
from reviewtrace.models.document import ReviewComment
from reviewtrace.models.evidence import EvidenceDirection
from reviewtrace.models.finding import STATUS_ORDER, Finding, Status
from reviewtrace.models.issue import ReviewIssue
from reviewtrace.models.report import AuditReport
from reviewtrace.reports.matrix import MARKS
from reviewtrace.utils.text import strip_control, truncate


class Text(RichText):
    """Rich ``Text`` that strips control characters (e.g. ESC) from untrusted input."""

    def __init__(self, text: str = "", *args: Any, **kwargs: Any) -> None:
        super().__init__(strip_control(text), *args, **kwargs)

    def append(self, text: Any, style: Any = None) -> RichText:
        if isinstance(text, str):
            text = strip_control(text)
        return super().append(text, style)


STATUS_STYLE = {
    Status.RESOLVED: "bold green",
    Status.PARTIALLY_RESOLVED: "bold yellow",
    Status.UNRESOLVED: "bold red",
    Status.NEEDS_REVIEW: "bold cyan",
    Status.NOT_ASSESSABLE: "bold dim",
    Status.ERROR: "bold white on red",
}
MARK_STYLE = {
    EvidenceDirection.SUPPORTS: "green",
    EvidenceDirection.CONTRADICTS: "red",
    EvidenceDirection.CONTEXT: "dim",
}


def _line(label: str, value: str, style: str = "") -> Text:
    t = Text()
    t.append(f"{label} ", style="bold")
    t.append(value, style=style)
    return t


def _finding_panel(
    report: AuditReport, issue: ReviewIssue, f: Finding, verbose: bool
) -> RenderableType:
    parts: list[RenderableType] = []
    parts.append(_line("Reviewer:", issue.reviewer or "unknown"))
    parts.append(Text(f"“{truncate(issue.comment_text, 400)}”", style="italic"))
    if issue.anchor_text:
        parts.append(_line("Anchored text:", f"“{truncate(issue.anchor_text, 200)}”", "dim"))
    head = Text()
    head.append("\n")
    head.append(f.status.label, style=STATUS_STYLE[f.status])
    head.append(f"   Confidence: {f.confidence.value}", style="bold")
    if f.requires_human_review:
        head.append("   (human review recommended)", style="dim")
    parts.append(head)

    flags = Text()
    flags.append("Reviewer's tool: ", style="dim")
    flags.append(
        {None: "n/a", True: "marked resolved", False: "not marked resolved"}[
            f.word_comment_resolved
        ],
        style="dim",
    )
    flags.append("  ·  Document: ", style="dim")
    flags.append(
        {None: "target unknown", True: "changed", False: "unchanged"}[f.document_changed],
        style="dim",
    )
    flags.append(f"  ·  Basis: {f.assessment_source.value}/{f.method or 'n/a'}", style="dim")
    parts.append(flags)

    if f.evidence:
        parts.append(Text("\nEvidence:", style="bold"))
        for e in f.evidence[: (12 if verbose else 5)]:
            t = Text()
            t.append(f"{MARKS[e.direction]} ", style=MARK_STYLE[e.direction])
            t.append(e.explanation)
            parts.append(t)
    for m in f.missing_elements:
        t = Text()
        t.append("- ", style="red")
        t.append(m)
        parts.append(t)
    locs = [loc.label() for loc in f.revised_locations if loc.label() != "unlocated"]
    if locs:
        parts.append(Text("\nRevised location:", style="bold"))
        parts.append(Text("; ".join(dict.fromkeys(locs[:3]))))
    if f.remaining_action:
        parts.append(Text("\nRemaining action:", style="bold"))
        parts.append(Text(f.remaining_action))
    if verbose or f.status in (Status.NEEDS_REVIEW, Status.NOT_ASSESSABLE, Status.ERROR):
        parts.append(Text(f"\nRationale: {f.rationale}", style="dim"))
    if f.located_by and verbose:
        parts.append(Text(f"Located by: {f.located_by}", style="dim"))
    return Panel(
        Group(*parts),
        title=Text(issue.id, style="bold"),
        title_align="left",
        border_style=STATUS_STYLE[f.status].split()[-1],
    )


def render_report(
    report: AuditReport, console: Console, *, verbose: bool = False, summary_only: bool = False
) -> None:
    console.print(Text("REVIEWTRACE", style="bold"))
    console.print(
        Text(
            f"{report.mode.value.replace('_', ' ')} · semantic assessment: {report.semantic_reviewer}",
            style="dim",
        )
    )
    console.print()
    counts = report.status_counts()
    table = RichTable(show_header=False, box=None, padding=(0, 2))
    table.add_column(justify="left")
    table.add_column(justify="right")
    table.add_row(
        Text("Review issues:", style="bold"), Text(str(len(report.findings)), style="bold")
    )
    for s in STATUS_ORDER:
        if counts[s] or s in (Status.RESOLVED, Status.UNRESOLVED):
            table.add_row(Text(s.label, style=STATUS_STYLE[s]), Text(str(counts[s])))
    console.print(table)
    if not summary_only:
        console.print(Text("─" * min(console.width, 60), style="dim"))
        for issue in report.issues:
            f = report.finding_for(issue.id)
            if f is not None:
                console.print(_finding_panel(report, issue, f, verbose))
    for w in report.warnings:
        console.print(Text(f"note: {w}", style="yellow"))


def render_comments(comments: list[ReviewComment], console: Console) -> None:
    if not comments:
        console.print(Text("No Word comments found.", style="yellow"))
        return
    console.print(Text(f"{len(comments)} comment(s)", style="bold"))
    for c in comments:
        title = Text()
        title.append(f"#{c.id}", style="bold")
        title.append(f"  {c.author or 'unknown'}", style="cyan")
        if c.date:
            title.append(f"  {c.date}", style="dim")
        if c.parent_id is not None:
            title.append(f"  (reply to #{c.parent_id})", style="dim")
        if c.word_comment_resolved is not None:
            title.append(
                "  [Word: resolved]" if c.word_comment_resolved else "  [Word: open]", style="dim"
            )
        body: list[RenderableType] = [Text(c.text)]
        if c.anchor_text:
            body.append(_line("\nAnchored text:", f"“{truncate(c.anchor_text, 240)}”", "dim"))
        where = c.anchor.label()
        if where != "unlocated":
            body.append(_line("Location:", where, "dim"))
        if c.context_before or c.context_after:
            if c.context_before:
                body.append(_line("Before:", truncate(c.context_before[-1], 160), "dim"))
            if c.context_after:
                body.append(_line("After:", truncate(c.context_after[0], 160), "dim"))
        for r in c.replies:
            body.append(_line(f"\nReply by {r.author or 'unknown'}:", truncate(r.text, 200)))
        console.print(Panel(Group(*body), title=title, title_align="left"))


def render_issues(issues: list[ReviewIssue], console: Console) -> None:
    table = RichTable(title="Extracted issues", show_lines=True)
    for col in ("ID", "Reviewer", "Category", "Comment", "Anchor / location"):
        table.add_column(col, overflow="fold")
    for i in issues:
        where = i.anchor_location.label()
        anchor = truncate(i.anchor_text, 80) if i.anchor_text else ""
        parts = [p for p in (anchor, "" if where == "unlocated" else where) if p]
        comment = truncate(i.comment_text, 160)
        if len(i.sub_requirements) > 1:
            comment += "\n" + "\n".join(
                f"  {s.id} {truncate(s.text, 70)}" for s in i.sub_requirements
            )
        table.add_row(
            Text(i.id),
            Text(i.reviewer),
            Text(i.category.value),
            Text(comment),
            Text("\n".join(parts)),
        )
    console.print(table)


def render_diff(diff: DocumentDiff, console: Console, *, verbose: bool = False) -> None:
    s = diff.summary
    console.print(Text("DOCUMENT DIFF", style="bold"))
    console.print(
        Text(
            f"paragraphs: {s.paragraphs_unchanged} unchanged, {s.paragraphs_added} added, {s.paragraphs_removed} removed, "
            f"{s.paragraphs_modified} modified, {s.paragraphs_moved} moved",
        )
    )
    console.print(
        Text(
            f"sections: {s.sections_added} added, {s.sections_removed} removed, {s.sections_renamed} renamed; "
            f"tables: {s.tables_added} added, {s.tables_removed} removed, {s.tables_modified} modified; "
            f"references: +{s.references_added} / -{s.references_removed}; "
            f"tracked changes: {s.tracked_insertions} insertions, {s.tracked_deletions} deletions",
        )
    )
    changed_sections = [
        m for m in diff.sections if m.kind is not SectionMatchKind.EXACT or m.changed
    ]
    if changed_sections:
        table = RichTable(title="Sections", show_lines=False)
        for col in ("Original", "Revised", "Match", "Words", "+ / - / ~ / moved"):
            table.add_column(col, overflow="fold")
        for m in changed_sections:
            table.add_row(
                Text(m.original_heading or ("(front matter)" if m.original_id else "")),
                Text(m.revised_heading or ("(front matter)" if m.revised_id else "")),
                Text(m.kind.value),
                Text(f"{m.words_before} → {m.words_after}"),
                Text(
                    f"{m.paragraphs_added} / {m.paragraphs_removed} / {m.paragraphs_modified} / {m.paragraphs_moved_in}"
                ),
            )
        console.print(table)
    styles = {
        ChangeKind.ADDED: "green",
        ChangeKind.REMOVED: "red",
        ChangeKind.MODIFIED: "yellow",
        ChangeKind.MOVED: "magenta",
    }
    shown = [c for c in diff.paragraphs if c.changed]
    if shown:
        console.print(Text("\nParagraph changes", style="bold"))
    for c in shown[: (500 if verbose else 40)]:
        t = Text()
        t.append(f"{c.kind.value:9}", style=styles[c.kind])
        t.append(
            f" orig {c.original if c.original is not None else '-':>4} → rev {c.revised if c.revised is not None else '-':>4}  "
        )
        t.append(truncate(c.revised_text or c.original_text, 90), style="dim")
        console.print(t)
    if len(shown) > 40 and not verbose:
        console.print(Text(f"... {len(shown) - 40} more (use --verbose)", style="dim"))
    for tc in diff.tables:
        if tc.kind is TableChangeKind.UNCHANGED:
            continue
        label = (
            tc.revised_caption
            or tc.original_caption
            or f"table #{(tc.revised_index if tc.revised_index is not None else tc.original_index or 0) + 1}"
        )
        console.print(Text(f"table {tc.kind.value}: {label} ({tc.describe()})"))
    r = diff.references
    if r.changed:
        console.print(
            Text(
                f"references: {len(r.added)} added, {len(r.removed)} removed, {len(r.modified)} modified",
                style="bold",
            )
        )

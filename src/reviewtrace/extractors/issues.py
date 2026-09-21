"""Build :class:`ReviewIssue` objects from any source (Word comment, report item, YAML)."""

from __future__ import annotations

import re

from reviewtrace.analysis.classifier import build_sub_requirements, classify, detect_severity
from reviewtrace.analysis.request_spec import parse_request
from reviewtrace.models.document import CommentReply, Document, Location, ReviewComment
from reviewtrace.models.issue import (
    IssueCategory,
    IssueSource,
    ReviewIssue,
    Severity,
    SubRequirement,
)
from reviewtrace.utils.text import similarity, strip_control

MAX_ISSUE_CHARS = 20_000
DUPLICATE_THRESHOLD = 0.85
MAX_DUPLICATE_SCAN = 800

_QUOTE = re.compile(r"[\"“]([^\"”]{8,600})[\"”]")


def issue_id(n: int) -> str:
    return f"RT-{n:03d}"


def quoted_anchor(text: str) -> str:
    """The longest quoted passage of three or more words, if the reviewer quoted one."""
    best = ""
    for m in _QUOTE.finditer(text):
        cand = m.group(1).strip()
        if len(cand.split()) >= 3 and len(cand) > len(best):
            best = cand
    return best


def build_issue(
    id_: str,
    source: IssueSource,
    comment_text: str,
    *,
    source_id: str | None = None,
    reviewer: str = "",
    date: str | None = None,
    anchor_text: str = "",
    anchor_location: Location | None = None,
    surrounding_context: str = "",
    severity: Severity | None = None,
    severity_hint: Severity | None = None,
    category: IssueCategory | None = None,
    parts: list[str] | None = None,
    word_comment_resolved: bool | None = None,
    thread: list[CommentReply] | None = None,
    truncated: bool = False,
    hints: list[str] | None = None,
    infer_anchor: bool = False,
) -> ReviewIssue:
    text = strip_control(comment_text).strip()
    reviewer = strip_control(reviewer)
    anchor_text = strip_control(anchor_text)
    surrounding_context = strip_control(surrounding_context)
    if len(text) > MAX_ISSUE_CHARS:
        text, truncated = text[:MAX_ISSUE_CHARS] + "…", True
    cls = classify(text)
    spec = parse_request(text)
    location = (anchor_location or Location()).model_copy(deep=True)
    if (
        not location.section
        and not location.paragraph_start
        and location.table_index is None
        and spec.section_refs
    ):
        location.section = spec.section_refs[0]
    if infer_anchor and not anchor_text:
        anchor_text = quoted_anchor(text)
    found_hints = list(hints or [])
    found_hints += [f"table:{t}" for t in spec.table_refs] + [
        f"figure:{f}" for f in spec.figure_refs
    ]
    found_hints += [f"page:{p}" for p in spec.page_refs]

    issue = ReviewIssue(
        id=id_,
        source=source,
        source_id=source_id,
        reviewer=reviewer,
        date=date,
        comment_text=text,
        anchor_text=anchor_text.strip(),
        anchor_location=location,
        surrounding_context=surrounding_context,
        category=category or cls.category,
        requested_action=cls.requested_action,
        severity=severity or detect_severity(text, severity_hint),
        word_comment_resolved=word_comment_resolved,
        thread=thread or [],
        actionable=cls.actionable or category is not None,
        subjective=cls.subjective,
        hints=found_hints,
        truncated=truncated,
    )
    if parts:
        issue.sub_requirements = [
            SubRequirement(id=f"{id_}.{i}", text=p, category=classify(p).category)
            for i, p in enumerate(parts, start=1)
        ]
    elif issue.actionable:
        issue.sub_requirements = build_sub_requirements(id_, text)
    return issue


def _context_string(c: ReviewComment) -> str:
    lines: list[str] = []
    if c.anchor.section:
        lines.append(f"Section: {c.anchor.section}")
    lines.extend(f"Before: {t}" for t in c.context_before)
    if c.anchor_paragraph_text:
        lines.append(f"Paragraph: {c.anchor_paragraph_text}")
    lines.extend(f"After: {t}" for t in c.context_after)
    return "\n".join(lines)


def issues_from_comments(doc: Document) -> list[ReviewIssue]:
    """One issue per top-level Word comment, numbered in document order."""
    top = [c for c in doc.comments if c.parent_id is None]

    def key(c: ReviewComment) -> tuple[int, int, int]:
        loc = c.anchor
        pos = loc.paragraph_start if loc.paragraph_start is not None else 10**9
        try:
            cid = int(c.id)
        except ValueError:
            cid = 10**9
        return (pos, cid, 0)

    issues: list[ReviewIssue] = []
    for n, c in enumerate(sorted(top, key=key), start=1):
        issues.append(
            build_issue(
                issue_id(n),
                IssueSource.WORD_COMMENT,
                c.text,
                source_id=c.id,
                reviewer=c.author,
                date=c.date,
                anchor_text=c.anchor_text,
                anchor_location=c.anchor,
                surrounding_context=_context_string(c),
                word_comment_resolved=c.word_comment_resolved,
                thread=c.replies,
                truncated=c.truncated,
            )
        )
    return mark_duplicates(issues)


def mark_duplicates(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    """Record probable duplicates. Issues are never merged: provenance stays separate."""
    if len(issues) > MAX_DUPLICATE_SCAN:
        return issues
    for i, a in enumerate(issues):
        for b in issues[i + 1 :]:
            if not a.comment_text or not b.comment_text:
                continue
            score = similarity(a.comment_text, b.comment_text)
            same_anchor = (
                bool(a.anchor_text) and a.anchor_text == b.anchor_text and a.category is b.category
            )
            if score >= DUPLICATE_THRESHOLD or (same_anchor and score >= 0.6):
                if b.id not in a.possible_duplicates:
                    a.possible_duplicates.append(b.id)
                if a.id not in b.possible_duplicates:
                    b.possible_duplicates.append(a.id)
    return issues

"""ReviewTrace core: the one engine behind the CLI, the MCP server and the reports.

Nothing in here writes to the input files. Documents are opened read-only and every
audit records the SHA-256 of each input so that "the source was not modified" can be
checked afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from reviewtrace.analysis.evidence_finder import AuditContext
from reviewtrace.analysis.heuristics import assess_issue
from reviewtrace.diff.document_diff import diff_documents
from reviewtrace.extractors.docx import load_document
from reviewtrace.extractors.issues import issues_from_comments
from reviewtrace.extractors.issues_file import load_issues_file
from reviewtrace.extractors.reviewer_report import extract_report
from reviewtrace.models.diff import DocumentDiff
from reviewtrace.models.document import Document, ReviewComment
from reviewtrace.models.finding import Confidence, Finding, Status
from reviewtrace.models.issue import IssueSource, ReviewIssue
from reviewtrace.models.report import AuditMode, AuditReport, InputFile
from reviewtrace.semantic.base import SemanticError, SemanticReviewer, merge_assessment
from reviewtrace.semantic.disabled import DisabledSemanticReviewer
from reviewtrace.utils.errors import ReviewTraceError
from reviewtrace.utils.files import sha256_of
from reviewtrace.utils.text import truncate


@dataclass
class IssueExtraction:
    issues: list[ReviewIssue]
    source: IssueSource
    warnings: list[str] = field(default_factory=list)


def extract_comments(path: str | Path) -> list[ReviewComment]:
    """Every Word comment (including replies) with anchors and context."""
    return load_document(path).comments


def extract_issues(path: str | Path, kind: str | None = None) -> IssueExtraction:
    """Extract issues from a DOCX with comments, or from a reviewer report.

    ``kind`` may be ``"comments"`` or ``"report"`` to force one reading; otherwise a DOCX
    that contains Word comments is read as comments and anything else as a report.
    """
    p = Path(path)
    if kind not in (None, "comments", "report"):
        raise ReviewTraceError(f"unknown extraction kind '{kind}' (use 'comments' or 'report')")
    if p.suffix.lower() == ".docx" and kind != "report":
        doc = load_document(p)
        if any(c.parent_id is None for c in doc.comments) or kind == "comments":
            issues = issues_from_comments(doc)
            warnings = list(doc.warnings)
            if not issues:
                warnings.append("the document contains no Word comments")
            return IssueExtraction(issues, IssueSource.WORD_COMMENT, warnings)
    extraction = extract_report(p)
    return IssueExtraction(extraction.issues, IssueSource.REVIEW_REPORT, extraction.warnings)


def compare_documents(
    original: str | Path, revised: str | Path
) -> tuple[DocumentDiff, Document, Document]:
    o = load_document(original, comments=False)
    r = load_document(revised, comments=False)
    return diff_documents(o, r), o, r


def _input(role: str, path: Path) -> InputFile:
    digest, size = sha256_of(path)
    return InputFile(role=role, path=str(path), sha256=digest, size_bytes=size)


def _context_text(doc: Document, numbers: list[int], radius: int = 1) -> str:
    if not numbers:
        return ""
    lo, hi = min(numbers) - radius, max(numbers) + radius
    return "\n".join(p.text for p in doc.paragraphs if lo <= p.number <= hi)


def _error_finding(issue: ReviewIssue, exc: Exception) -> Finding:
    return Finding(
        issue_id=issue.id,
        status=Status.ERROR,
        confidence=Confidence.LOW,
        rationale=f"A technical problem prevented assessment: {type(exc).__name__}: {truncate(str(exc), 200)}",
        requires_human_review=True,
        method="error",
        word_comment_resolved=issue.word_comment_resolved,
    )


def _apply_semantic(
    finding: Finding,
    issue: ReviewIssue,
    ctx: AuditContext,
    reviewer: SemanticReviewer,
    warnings: list[str],
) -> Finding:
    if finding.status not in (Status.NEEDS_REVIEW, Status.NOT_ASSESSABLE):
        return finding
    if finding.status is Status.NOT_ASSESSABLE and not finding.evidence:
        return finding
    o_nums = [
        n
        for e in finding.evidence
        if e.original_location and (n := e.original_location.paragraph_start)
    ]
    r_nums = [
        n
        for e in finding.evidence
        if e.revised_location and (n := e.revised_location.paragraph_start)
    ]
    o_ctx = issue.anchor_text or _context_text(ctx.original, o_nums)
    r_ctx = _context_text(ctx.revised, r_nums)
    try:
        assessment = reviewer.assess(issue, o_ctx, r_ctx, finding.evidence)
        return merge_assessment(finding, assessment)
    except SemanticError as exc:
        warnings.append(f"{issue.id}: semantic review failed ({exc}); deterministic result kept")
        return finding


def run_audit(
    mode: AuditMode,
    original: Document,
    revised: Document,
    issues: list[ReviewIssue],
    inputs: list[InputFile],
    *,
    semantic: SemanticReviewer | None = None,
    warnings: list[str] | None = None,
) -> AuditReport:
    diff = diff_documents(original, revised)
    ctx = AuditContext(original, revised, diff)
    reviewer = semantic or DisabledSemanticReviewer()
    notes = list(warnings or [])
    findings: list[Finding] = []
    for issue in issues:
        try:
            finding = assess_issue(issue, ctx)
        except Exception as exc:  # one bad issue must never sink the whole audit
            finding = _error_finding(issue, exc)
        if reviewer.enabled:
            try:
                finding = _apply_semantic(finding, issue, ctx, reviewer, notes)
            except Exception as exc:
                notes.append(
                    f"{issue.id}: semantic review error ({type(exc).__name__}); deterministic result kept"
                )
        findings.append(finding)
    assessed = [
        i.model_copy(update={"status": f.status}) for i, f in zip(issues, findings, strict=True)
    ]
    return AuditReport(
        mode=mode,
        inputs=inputs,
        semantic_reviewer=reviewer.name,
        issues=assessed,
        findings=findings,
        diff_summary=diff.summary,
        warnings=notes,
    )


def audit_word_comments(
    reviewed: str | Path, revised: str | Path, *, semantic: SemanticReviewer | None = None
) -> AuditReport:
    """Mode A: ``reviewed.docx`` carries the comments; ``revised.docx`` is the response."""
    rp, vp = Path(reviewed), Path(revised)
    original = load_document(rp)
    issues = issues_from_comments(original)
    if not issues:
        raise ReviewTraceError(
            f"{rp.name} contains no Word comments; use --review with a reviewer report or --issues with an issues file"
        )
    rev = load_document(vp, comments=False)
    return run_audit(
        AuditMode.WORD_COMMENTS,
        original,
        rev,
        issues,
        [_input("reviewed", rp), _input("revised", vp)],
        semantic=semantic,
        warnings=list(original.warnings),
    )


def audit_review_report(
    original: str | Path,
    review: str | Path,
    revised: str | Path,
    *,
    semantic: SemanticReviewer | None = None,
) -> AuditReport:
    """Mode B: separate reviewer report (.docx/.md/.txt)."""
    op, rvp, vp = Path(original), Path(review), Path(revised)
    extraction = extract_report(rvp)
    if not extraction.issues:
        raise ReviewTraceError(f"no review items could be extracted from {rvp.name}")
    return run_audit(
        AuditMode.REVIEW_REPORT,
        load_document(op, comments=False),
        load_document(vp, comments=False),
        extraction.issues,
        [_input("original", op), _input("review", rvp), _input("revised", vp)],
        semantic=semantic,
        warnings=extraction.warnings,
    )


def audit_issue_file(
    original: str | Path,
    issues_path: str | Path,
    revised: str | Path,
    *,
    semantic: SemanticReviewer | None = None,
) -> AuditReport:
    """Mode C: the user supplies the known requested corrections."""
    op, ip, vp = Path(original), Path(issues_path), Path(revised)
    issues = load_issues_file(ip)
    if not issues:
        raise ReviewTraceError(f"{ip.name} contains no issues")
    return run_audit(
        AuditMode.ISSUES_FILE,
        load_document(op, comments=False),
        load_document(vp, comments=False),
        issues,
        [_input("original", op), _input("issues", ip), _input("revised", vp)],
        semantic=semantic,
    )

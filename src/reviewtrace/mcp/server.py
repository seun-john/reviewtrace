"""MCP server exposing ReviewTrace to MCP clients.

Every tool is a thin wrapper over :mod:`reviewtrace.core` and the report writers; there
is no assessment logic here. Set ``REVIEWTRACE_MCP_ROOT`` to confine file access to one
directory. The server runs locally over stdio and makes no network calls.

Requires the optional dependency:  pip install "reviewtrace[mcp]"
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from reviewtrace import __version__, core
from reviewtrace.extractors.issues_file import issues_to_yaml
from reviewtrace.models.report import AuditReport
from reviewtrace.reports.csv_report import render_csv
from reviewtrace.reports.json_report import load_report, report_from_dict
from reviewtrace.reports.markdown import render_matrix_markdown
from reviewtrace.reports.matrix import build_matrix
from reviewtrace.reports.response_letter import generate_response
from reviewtrace.utils.errors import ReviewTraceError
from reviewtrace.utils.files import confine

INSTRUCTIONS = (
    "ReviewTrace audits whether reviewer feedback was addressed in a revised DOCX. Statuses are "
    "RESOLVED, PARTIALLY_RESOLVED, UNRESOLVED, NEEDS_REVIEW, NOT_ASSESSABLE and ERROR. Every status "
    "carries evidence and a separate confidence; NEEDS_REVIEW means adequacy could not be judged "
    "automatically. All processing is local."
)


def _path(value: str) -> str:
    return str(confine(value))


def _report(audit: dict[str, Any] | None, audit_file: str | None) -> AuditReport:
    if audit is not None:
        return report_from_dict(audit)
    if audit_file:
        return load_report(_path(audit_file))
    raise ReviewTraceError("provide either 'audit' (the result of audit_revision) or 'audit_file'")


def extract_review_comments(file: str) -> dict[str, Any]:
    """List every Word comment in a .docx with author, date, anchored text and context."""
    comments = core.extract_comments(_path(file))
    return {"count": len(comments), "comments": [c.model_dump(mode="json") for c in comments]}


def extract_review_issues(file: str, kind: str | None = None) -> dict[str, Any]:
    """Extract review issues from a .docx with comments or from a reviewer report (.docx/.md/.txt).

    ``kind`` may be "comments" or "report" to force one reading.
    """
    result = core.extract_issues(_path(file), kind)
    return {
        "source": result.source.value,
        "count": len(result.issues),
        "warnings": result.warnings,
        "issues": [i.model_dump(mode="json") for i in result.issues],
        "issues_yaml": issues_to_yaml(result.issues),
    }


def compare_documents(original_file: str, revised_file: str) -> dict[str, Any]:
    """Structural comparison of two .docx files: sections, paragraphs, tables and references."""
    diff, _o, _r = core.compare_documents(_path(original_file), _path(revised_file))
    return diff.model_dump(mode="json")


def audit_revision(
    reviewed_file: str | None = None,
    revised_file: str | None = None,
    original_file: str | None = None,
    review_file: str | None = None,
    issues_file: str | None = None,
) -> dict[str, Any]:
    """Audit whether reviewer feedback was addressed.

    Word comments:     reviewed_file + revised_file
    Reviewer report:   original_file + review_file + revised_file
    Issues file:       original_file + issues_file + revised_file
    """
    if not revised_file:
        raise ReviewTraceError("revised_file is required")
    revised = _path(revised_file)
    if reviewed_file:
        if original_file or review_file or issues_file:
            raise ReviewTraceError(
                "reviewed_file cannot be combined with original_file, review_file or issues_file"
            )
        report = core.audit_word_comments(_path(reviewed_file), revised)
    elif original_file and review_file and not issues_file:
        report = core.audit_review_report(_path(original_file), _path(review_file), revised)
    elif original_file and issues_file and not review_file:
        report = core.audit_issue_file(_path(original_file), _path(issues_file), revised)
    else:
        raise ReviewTraceError(
            "provide reviewed_file, or original_file with exactly one of review_file / issues_file"
        )
    return report.model_dump(mode="json")


def inspect_issue(
    issue_id: str, audit: dict[str, Any] | None = None, audit_file: str | None = None
) -> dict[str, Any]:
    """Return one issue with its finding and full evidence trail."""
    report = _report(audit, audit_file)
    issue = report.issue_for(issue_id.upper())
    finding = report.finding_for(issue_id.upper())
    if issue is None or finding is None:
        raise ReviewTraceError(f"no issue '{issue_id}' in this audit")
    return {"issue": issue.model_dump(mode="json"), "finding": finding.model_dump(mode="json")}


def generate_traceability_matrix(
    audit: dict[str, Any] | None = None, audit_file: str | None = None, format: str = "json"
) -> dict[str, Any]:
    """Build the traceability matrix as "json" (rows), "markdown" or "csv"."""
    report = _report(audit, audit_file)
    if format == "markdown":
        return {"format": "markdown", "content": render_matrix_markdown(report)}
    if format == "csv":
        return {"format": "csv", "content": render_csv(report)}
    if format == "json":
        return {"format": "json", "rows": [r.as_dict() for r in build_matrix(report)]}
    raise ReviewTraceError("format must be 'json', 'markdown' or 'csv'")


def generate_response_to_reviewers(
    audit: dict[str, Any] | None = None,
    audit_file: str | None = None,
    include_excerpts: bool = False,
) -> dict[str, Any]:
    """Draft a response-to-reviewers letter grounded in the audit findings."""
    draft = generate_response(_report(audit, audit_file), excerpts=include_excerpts)
    return {"markdown": draft.text, "needs_author_attention": draft.caveats}


TOOLS: list[Callable[..., dict[str, Any]]] = [
    extract_review_comments,
    extract_review_issues,
    compare_documents,
    audit_revision,
    inspect_issue,
    generate_traceability_matrix,
    generate_response_to_reviewers,
]


def _tool_error_type() -> type[Exception]:
    try:
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:
        import importlib

        return importlib.import_module("mcp.server.fastmcp.exceptions").ToolError  # type: ignore[no-any-return]
    return ToolError


def _user_facing(
    fn: Callable[..., dict[str, Any]], tool_error: type[Exception]
) -> Callable[..., dict[str, Any]]:
    """Report expected ReviewTrace failures to the client with their real message."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except ReviewTraceError as exc:
            raise tool_error(str(exc)) from exc

    return wrapper


def build_server() -> Any:
    """Create the server. Supports both the MCP 2.x (MCPServer) and 1.x (FastMCP) SDKs."""
    server_cls: Any
    try:
        from mcp.server.mcpserver import MCPServer as server_cls
    except ImportError:
        import importlib

        server_cls = importlib.import_module("mcp.server.fastmcp").FastMCP

    try:
        server = server_cls(name="reviewtrace", instructions=INSTRUCTIONS, version=__version__)
    except TypeError:  # 1.x does not take ``version``
        server = server_cls(name="reviewtrace", instructions=INSTRUCTIONS)
    tool_error = _tool_error_type()
    for fn in TOOLS:
        server.tool()(_user_facing(fn, tool_error))
    return server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()

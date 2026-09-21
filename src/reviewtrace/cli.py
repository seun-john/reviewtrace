"""Command-line interface. A thin layer over :mod:`reviewtrace.core` and the report writers."""

from __future__ import annotations

import functools
import os
import sys
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

import typer
from rich.console import Console

from reviewtrace import __version__, core
from reviewtrace.extractors.issues_file import issues_to_yaml
from reviewtrace.models.finding import Status
from reviewtrace.models.report import AuditReport
from reviewtrace.reports.csv_report import render_csv
from reviewtrace.reports.json_report import load_report, render_json, render_matrix_json
from reviewtrace.reports.markdown import md_escape, render_markdown, render_matrix_markdown
from reviewtrace.reports.response_letter import generate_response
from reviewtrace.reports.terminal import (
    Text,
    render_comments,
    render_diff,
    render_issues,
    render_report,
)
from reviewtrace.utils.errors import ReviewTraceError

app = typer.Typer(
    name="reviewtrace",
    help="Trace reviewer feedback to the evidence that it was addressed in a revised document.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)

F = TypeVar("F", bound=Callable[..., Any])
EXIT_FAIL_ON = 3


class ReportFormat(str, Enum):
    terminal = "terminal"
    markdown = "markdown"
    json = "json"
    csv = "csv"


class MatrixFormat(str, Enum):
    markdown = "markdown"
    csv = "csv"
    json = "json"


class DiffFormat(str, Enum):
    terminal = "terminal"
    json = "json"


class ExtractKind(str, Enum):
    comments = "comments"
    report = "report"


class FailOn(str, Enum):
    unresolved = "unresolved"
    not_resolved = "not-resolved"


def _ensure_utf8() -> None:
    """Windows consoles often default to a legacy code page that cannot print arrows or quotes."""
    for stream in (sys.stdout, sys.stderr):
        try:
            enc = (stream.encoding or "").lower().replace("-", "")
            if enc != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _consoles() -> tuple[Console, Console]:
    return Console(), Console(stderr=True)


def guarded(fn: F) -> F:
    """Turn expected failures into a one-line message and a non-zero exit code."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ReviewTraceError as exc:
            _consoles()[1].print(Text(f"error: {exc}", style="bold red"))
            raise typer.Exit(1) from exc
        except (typer.Exit, typer.BadParameter, typer.Abort):
            raise
        except Exception as exc:
            if os.environ.get("REVIEWTRACE_DEBUG"):
                raise
            _consoles()[1].print(
                Text(
                    f"unexpected error: {type(exc).__name__}: {exc} (set REVIEWTRACE_DEBUG=1 for a traceback)",
                    style="bold red",
                )
            )
            raise typer.Exit(1) from exc

    return wrapper  # type: ignore[return-value]


def _version(value: bool) -> None:
    if value:
        typer.echo(f"reviewtrace {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    _ensure_utf8()


def _write(path: Path, text: str, protected: list[Path]) -> None:
    """Write ``text`` to ``path``, refusing to overwrite any input file."""
    target = path.resolve()
    if any(target == p.resolve() for p in protected):
        raise ReviewTraceError(f"refusing to overwrite an input file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _emit(text: str, output: Path | None, protected: list[Path], err: Console, label: str) -> None:
    if output is None:
        typer.echo(text, nl=False)
    else:
        _write(output, text, protected)
        err.print(Text(f"{label} written to {output}", style="green"))


# -------------------------------------------------------------------------------------
# comments
# -------------------------------------------------------------------------------------


@app.command()
@guarded
def comments(
    file: Path = typer.Argument(..., help="A .docx file containing Word comments."),
    as_json: bool = typer.Option(False, "--json", help="Print comments as JSON."),
) -> None:
    """Show every Word comment with its author, anchored text and surrounding context."""
    found = core.extract_comments(file)
    if as_json:
        import json

        typer.echo(
            json.dumps([c.model_dump(mode="json") for c in found], indent=2, ensure_ascii=False)
        )
        return
    out, _ = _consoles()
    render_comments(found, out)


# -------------------------------------------------------------------------------------
# extract
# -------------------------------------------------------------------------------------


@app.command()
@guarded
def extract(
    file: Path = typer.Argument(
        ..., help="A .docx with comments, or a reviewer report (.docx, .md, .txt)."
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write issues.yml here (default: print YAML)."
    ),
    kind: ExtractKind | None = typer.Option(
        None, "--as", help="Force reading as Word comments or as a report."
    ),
) -> None:
    """Extract review issues into an editable issues.yml."""
    out, err = _consoles()
    result = core.extract_issues(file, kind.value if kind else None)
    for w in result.warnings:
        err.print(Text(f"note: {w}", style="yellow"))
    text = issues_to_yaml(result.issues)
    if output is None:
        typer.echo(text, nl=False)
        return
    _write(output, text, [file])
    err.print(Text(f"{len(result.issues)} issue(s) written to {output}", style="green"))
    render_issues(result.issues, out)


# -------------------------------------------------------------------------------------
# diff
# -------------------------------------------------------------------------------------


@app.command()
@guarded
def diff(
    original: Path = typer.Argument(..., help="The original .docx."),
    revised: Path = typer.Argument(..., help="The revised .docx."),
    fmt: DiffFormat = typer.Option(DiffFormat.terminal, "--format", "-f", help="Output format."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="List every paragraph change."),
) -> None:
    """Compare two documents structurally (sections, paragraphs, tables, references)."""
    result, _o, _r = core.compare_documents(original, revised)
    if fmt is DiffFormat.json:
        typer.echo(result.model_dump_json(indent=2))
        return
    render_diff(result, _consoles()[0], verbose=verbose)


# -------------------------------------------------------------------------------------
# audit
# -------------------------------------------------------------------------------------


def _fails(report: AuditReport, mode: FailOn) -> bool:
    bad = {Status.UNRESOLVED, Status.PARTIALLY_RESOLVED, Status.ERROR}
    if mode is FailOn.not_resolved:
        return any(f.status is not Status.RESOLVED for f in report.findings)
    return any(f.status in bad for f in report.findings)


@app.command()
@guarded
def audit(
    reviewed: Path | None = typer.Argument(None, help="reviewed.docx containing Word comments."),
    revised_pos: Path | None = typer.Argument(
        None, help="revised.docx (with the positional reviewed.docx form)."
    ),
    original: Path | None = typer.Option(
        None, "--original", help="original.docx (three-file and issue-file workflows)."
    ),
    review: Path | None = typer.Option(
        None, "--review", help="Reviewer report: .docx, .md or .txt."
    ),
    issues: Path | None = typer.Option(
        None, "--issues", help="issues.yml with the requested corrections."
    ),
    revised: Path | None = typer.Option(None, "--revised", help="revised.docx."),
    fmt: ReportFormat = typer.Option(
        ReportFormat.terminal, "--format", "-f", help="Output format."
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write the report here instead of stdout."
    ),
    out_dir: Path | None = typer.Option(
        None,
        "--out-dir",
        help="Also write audit.json, audit.md, traceability.csv and response.md here.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show rationale and location details for every issue."
    ),
    fail_on: FailOn | None = typer.Option(
        None, "--fail-on", help="Exit with status 3 when findings match (for CI)."
    ),
) -> None:
    """Audit whether reviewer comments were addressed in a revised document.

    Word-comment workflow:  audit reviewed.docx revised.docx

    Reviewer-report workflow:  audit --original original.docx --review comments.docx --revised revised.docx

    Issue-file workflow:  audit --original original.docx --issues issues.yml --revised revised.docx
    """
    revised_path = revised_pos or revised
    if revised_pos is not None and revised is not None:
        raise typer.BadParameter(
            "give the revised document once: either positionally or with --revised"
        )
    if reviewed is not None:
        if original or review or issues:
            raise typer.BadParameter(
                "the reviewed.docx form cannot be combined with --original, --review or --issues"
            )
        if revised_path is None:
            raise typer.BadParameter(
                "missing the revised document (audit reviewed.docx revised.docx)"
            )
        report = core.audit_word_comments(reviewed, revised_path)
        protected = [reviewed, revised_path]
    else:
        if original is None or revised_path is None:
            raise typer.BadParameter(
                "provide either 'reviewed.docx revised.docx' or --original with --revised"
            )
        if (review is None) == (issues is None):
            raise typer.BadParameter(
                "provide exactly one of --review or --issues with --original/--revised"
            )
        if review is not None:
            report = core.audit_review_report(original, review, revised_path)
            protected = [original, review, revised_path]
        else:
            assert issues is not None
            report = core.audit_issue_file(original, issues, revised_path)
            protected = [original, issues, revised_path]

    out, err = _consoles()
    if out_dir is not None:
        _write(out_dir / "audit.json", render_json(report), protected)
        _write(out_dir / "audit.md", render_markdown(report), protected)
        _write(out_dir / "traceability.csv", render_csv(report), protected)
        draft = generate_response(report)
        _write(out_dir / "response.md", draft.text, protected)
        err.print(Text(f"reports written to {out_dir}", style="green"))
    if fmt is ReportFormat.terminal:
        if output is not None:
            raise typer.BadParameter("--output needs --format markdown, json or csv")
        render_report(report, out, verbose=verbose)
    elif fmt is ReportFormat.markdown:
        _emit(render_markdown(report), output, protected, err, "markdown report")
    elif fmt is ReportFormat.json:
        _emit(render_json(report), output, protected, err, "JSON report")
    else:
        _emit(render_csv(report), output, protected, err, "CSV matrix")
    if fail_on is not None and _fails(report, fail_on):
        raise typer.Exit(EXIT_FAIL_ON)


# -------------------------------------------------------------------------------------
# response / matrix / inspect
# -------------------------------------------------------------------------------------


@app.command()
@guarded
def response(
    audit_file: Path = typer.Argument(
        ..., help="An audit .json produced by 'reviewtrace audit -f json'."
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write the letter here (default: stdout)."
    ),
    excerpts: bool = typer.Option(
        False, "--excerpts", help="Quote the added text under 'Changes made'."
    ),
) -> None:
    """Draft a response-to-reviewers letter from an audit (no changes are invented)."""
    _, err = _consoles()
    report = load_report(audit_file)
    draft = generate_response(report, excerpts=excerpts)
    _emit(draft.text, output, [audit_file], err, "response draft")
    if draft.caveats:
        err.print(
            Text(
                f"note: {len(draft.caveats)} response(s) need author attention before sending "
                f"(not resolved, or resolved with less than HIGH confidence): {', '.join(draft.caveats)}",
                style="yellow",
            )
        )


@app.command()
@guarded
def matrix(
    audit_file: Path = typer.Argument(
        ..., help="An audit .json produced by 'reviewtrace audit -f json'."
    ),
    fmt: MatrixFormat = typer.Option(
        MatrixFormat.markdown, "--format", "-f", help="Output format."
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write the matrix here (default: stdout)."
    ),
) -> None:
    """Print the traceability matrix from an audit."""
    report = load_report(audit_file)
    text = {
        MatrixFormat.markdown: render_matrix_markdown,
        MatrixFormat.csv: render_csv,
        MatrixFormat.json: render_matrix_json,
    }[fmt](report)
    _emit(text, output, [audit_file], _consoles()[1], "matrix")


@app.command()
@guarded
def inspect(
    audit_file: Path = typer.Argument(
        ..., help="An audit .json produced by 'reviewtrace audit -f json'."
    ),
    issue_id: str = typer.Argument(..., help="Issue id, e.g. RT-004."),
) -> None:
    """Show one issue with its full evidence trail."""
    report = load_report(audit_file)
    issue = report.issue_for(issue_id.upper())
    if issue is None:
        known = ", ".join(i.id for i in report.issues)
        raise ReviewTraceError(f"no issue '{issue_id}' in {audit_file.name} (known: {known})")
    one = report.model_copy(
        update={
            "issues": [issue],
            "findings": [f for f in report.findings if f.issue_id == issue.id],
            "warnings": [],
        }
    )
    render_report(one, _consoles()[0], verbose=True)
    if issue.thread:
        typer.echo("")
        typer.echo(f"Thread ({len(issue.thread)} repl{'y' if len(issue.thread) == 1 else 'ies'}):")
        for r in issue.thread:
            typer.echo(f"  {r.author or 'unknown'}: {md_escape(r.text)}")


# -------------------------------------------------------------------------------------
# mcp
# -------------------------------------------------------------------------------------


@app.command()
@guarded
def mcp() -> None:
    """Run the MCP server on stdio (requires: pip install 'reviewtrace[mcp]')."""
    try:
        from reviewtrace.mcp.server import main as run_server
    except ImportError as exc:
        raise ReviewTraceError(
            "the MCP server needs the optional dependency: pip install 'reviewtrace[mcp]'"
        ) from exc
    run_server()


if __name__ == "__main__":  # pragma: no cover
    app()

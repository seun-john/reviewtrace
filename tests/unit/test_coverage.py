"""The shared coverage block: how many comments got a verdict, and why not the rest."""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from reviewtrace import core
from reviewtrace.cli import app
from reviewtrace.models.finding import Confidence, Finding
from reviewtrace.models.finding import Status as St
from reviewtrace.models.issue import IssueCategory, IssueSource, ReviewIssue
from reviewtrace.models.report import AuditMode, AuditReport, decided_share, describe_coverage
from reviewtrace.reports.json_report import load_report, render_json
from reviewtrace.reports.markdown import render_markdown

runner = CliRunner()


def synthetic(*pairs: tuple[St, IssueCategory]) -> AuditReport:
    issues, findings = [], []
    for n, (status, category) in enumerate(pairs, start=1):
        ident = f"RT-{n:03d}"
        issues.append(
            ReviewIssue(
                id=ident, source=IssueSource.ISSUES_FILE, comment_text="c", category=category
            )
        )
        findings.append(
            Finding(
                issue_id=ident,
                status=status,
                confidence=Confidence.MEDIUM,
                rationale="r",
                evidence=[],
            )
            if status is not St.RESOLVED
            else _resolved(ident)
        )
    return AuditReport(mode=AuditMode.ISSUES_FILE, issues=issues, findings=findings)


def _resolved(ident: str) -> Finding:
    from reviewtrace.models.evidence import Evidence, EvidenceDirection, EvidenceType

    support = Evidence(
        type=EvidenceType.TEXT_ADDED, direction=EvidenceDirection.SUPPORTS, explanation="e"
    )
    return Finding(
        issue_id=ident,
        status=St.RESOLVED,
        confidence=Confidence.HIGH,
        rationale="r",
        evidence=[support],
    )


def test_decided_means_resolved_partly_resolved_or_unresolved() -> None:
    report = synthetic(
        (St.RESOLVED, IssueCategory.ADD),
        (St.PARTIALLY_RESOLVED, IssueCategory.EXPAND),
        (St.UNRESOLVED, IssueCategory.CORRECT),
        (St.NEEDS_REVIEW, IssueCategory.CLARIFY),
        (St.NOT_ASSESSABLE, IssueCategory.EXPLAIN),
        (St.NOT_ASSESSABLE, IssueCategory.EXPLAIN),
        (St.NOT_ASSESSABLE, IssueCategory.FIGURE),
        (St.ERROR, IssueCategory.OTHER),
    )
    block = report.coverage
    assert (block["unit"], block["items"], block["decided"], block["undecided"]) == (
        "comments",
        8,
        3,
        5,
    )
    assert block["reasons"] == {
        "error": 1,
        "needs_review": 1,
        "not_assessable_explain": 2,
        "not_assessable_figure": 1,
    }
    assert block["decided"] + block["undecided"] == block["items"]
    assert sum(block["reasons"].values()) == block["undecided"]
    assert block["suppressed"] == 0


def test_no_comments_means_no_share() -> None:
    report = synthetic()
    assert report.coverage["items"] == 0
    assert decided_share(report.coverage) is None


def test_describe_is_one_line_with_the_biggest_reason_first() -> None:
    report = synthetic(
        (St.RESOLVED, IssueCategory.ADD),
        (St.NOT_ASSESSABLE, IssueCategory.EXPLAIN),
        (St.NOT_ASSESSABLE, IssueCategory.EXPLAIN),
        (St.NEEDS_REVIEW, IssueCategory.OTHER),
    )
    assert describe_coverage(report.coverage) == (
        "decided 1 of 4 comments; 3 could not be checked (not_assessable_explain 2, needs_review 1)"
    )


def test_coverage_is_in_the_json_report_and_survives_loading(tmp_path: Path) -> None:
    report = synthetic((St.NEEDS_REVIEW, IssueCategory.OTHER), (St.UNRESOLVED, IssueCategory.ADD))
    data = json.loads(render_json(report))
    assert data["coverage"]["items"] == 2 and data["coverage"]["reasons"] == {"needs_review": 1}
    path = tmp_path / "audit.json"
    path.write_text(render_json(report), encoding="utf-8")
    again = load_report(path)
    assert again.coverage == report.coverage
    for old in ("findings", "issues", "mode", "diff_summary", "warnings"):
        assert old in data


def test_markdown_report_has_the_coverage_line() -> None:
    report = synthetic((St.NEEDS_REVIEW, IssueCategory.OTHER))
    text = render_markdown(report)
    assert "Coverage: decided 0 of 1 comments; 1 could not be checked (needs_review 1)." in text


def test_the_demo_audit_decides_two_of_four(demo_files) -> None:
    report = core.audit_word_comments(demo_files["reviewed"], demo_files["revised"])
    block = report.coverage
    assert (block["items"], block["decided"], block["undecided"]) == (4, 2, 2)
    assert block["reasons"] == {"needs_review": 2}


def test_terminal_report_states_coverage(demo_files) -> None:
    result = runner.invoke(app, ["audit", str(demo_files["reviewed"]), str(demo_files["revised"])])
    assert result.exit_code == 0
    flat = re.sub(r"\s+", " ", result.output)
    assert "Coverage: decided 2 of 4 comments; 2 could not be checked (needs_review 2)" in flat


def test_min_coverage_exit_code(demo_files) -> None:
    args = ["audit", str(demo_files["reviewed"]), str(demo_files["revised"]), "-f", "json"]
    assert runner.invoke(app, [*args, "--min-coverage", "50"]).exit_code == 0
    assert runner.invoke(app, [*args, "--min-coverage", "51"]).exit_code == 4
    assert runner.invoke(app, args).exit_code == 0  # opt-in


def test_fail_on_wins_over_low_coverage(demo_files) -> None:
    args = ["audit", str(demo_files["reviewed"]), str(demo_files["revised"]), "-f", "json"]
    result = runner.invoke(app, [*args, "--fail-on", "unresolved", "--min-coverage", "100"])
    assert result.exit_code == 3

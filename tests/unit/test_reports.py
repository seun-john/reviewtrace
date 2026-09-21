from __future__ import annotations

import csv
import io
import json
import re
from io import StringIO

import pytest
from rich.console import Console

from reviewtrace import core
from reviewtrace.models.finding import Status as St
from reviewtrace.reports.csv_report import render_csv, safe_cell
from reviewtrace.reports.json_report import (
    load_report,
    render_json,
    render_matrix_json,
    report_from_dict,
)
from reviewtrace.reports.markdown import md_escape, render_markdown, render_matrix_markdown
from reviewtrace.reports.matrix import CORE_COLUMNS, build_matrix
from reviewtrace.reports.response_letter import generate_response
from reviewtrace.reports.terminal import render_report
from reviewtrace.utils.errors import ReviewTraceError
from tests.fixtures.docx_builder import DocxBuilder
from tests.helpers import audit_pair, make_issue


@pytest.fixture()
def report(demo_files):
    return core.audit_word_comments(demo_files["reviewed"], demo_files["revised"])


def test_demo_statuses(report):
    assert [f.status for f in report.findings] == [
        St.RESOLVED,
        St.NEEDS_REVIEW,
        St.NEEDS_REVIEW,
        St.UNRESOLVED,
    ]
    assert [i.status for i in report.issues] == [f.status for f in report.findings]


def test_json_round_trip(report, tmp_path):
    text = render_json(report)
    data = json.loads(text)
    assert data["schema_version"] == 1 and data["semantic_reviewer"] == "disabled"
    assert len(data["inputs"]) == 2 and len(data["inputs"][0]["sha256"]) == 64
    p = tmp_path / "audit.json"
    p.write_text(text, encoding="utf-8")
    again = load_report(p)
    assert again.model_dump() == report.model_dump()


def test_json_rejects_bad_input(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ReviewTraceError, match="not valid JSON"):
        load_report(p)
    with pytest.raises(ReviewTraceError):
        report_from_dict({"schema_version": 99})
    with pytest.raises(ReviewTraceError):
        report_from_dict([])
    with pytest.raises(ReviewTraceError, match="not found"):
        load_report(tmp_path / "missing.json")


def test_markdown_report_contains_matrix_and_evidence(report):
    md = render_markdown(report)
    assert "## Traceability matrix" in md and "### RT-004 — UNRESOLVED" in md
    assert "| ID | Reviewer | Comment |" in md
    assert "Reviewer's tool" in md and "no resolution metadata" in md
    assert "Section 2.1 Theoretical Framework" in md


def test_markdown_neutralises_hostile_text(tmp_path):
    hostile = "![x](http://evil.example/p.png) <script>alert(1)</script> [click](http://evil.example) | pipe"
    b = DocxBuilder().heading("1 Intro", 1).para("Some text here.")
    rep = audit_pair(tmp_path, b, b, make_issue(1, hostile))
    md = render_markdown(rep)
    assert not re.search(r"(?<!\\)[\[\]]", md)  # no unescaped link/image syntax
    assert not re.search(r"<(?!br>)", md)  # no raw HTML
    assert md_escape("[a](b)") == "\\[a\\](b)"
    letter = generate_response(rep).text
    assert not re.search(r"(?<!\\)[\[\]]", letter) and "<script>" not in letter


def test_csv_report_and_formula_injection(tmp_path):
    b = DocxBuilder().heading("1 Intro", 1).para("Some text here.")
    rep = audit_pair(tmp_path, b, b, make_issue(1, '=HYPERLINK("http://evil","x")'))
    rows = list(csv.reader(io.StringIO(render_csv(rep))))
    assert rows[0][:3] == ["ID", "Reviewer", "Comment"]
    assert rows[1][2].startswith("'=")
    assert safe_cell("+1") == "'+1" and safe_cell("-1") == "'-1" and safe_cell("@x") == "'@x"
    assert safe_cell("plain") == "plain"


def test_csv_of_demo(report):
    rows = list(csv.DictReader(io.StringIO(render_csv(report))))
    assert [r["ID"] for r in rows] == ["RT-001", "RT-002", "RT-003", "RT-004"]
    assert rows[0]["Status"] == "RESOLVED" and rows[3]["Status"] == "UNRESOLVED"
    assert rows[3]["Needs Human Review"] == "no" and rows[0]["Needs Human Review"] == "yes"
    assert rows[0]["Reviewer Marked Resolved"] == "n/a" and rows[0]["Document Changed"] == "yes"


def test_matrix_columns_and_json(report):
    assert [t for t, _ in CORE_COLUMNS] == [
        "ID", "Reviewer", "Comment", "Anchored Context", "Requested Action",
        "Status", "Confidence", "Evidence", "Revised Location", "Remaining Issue",
    ]  # fmt: skip
    md = render_matrix_markdown(report)
    assert md.count("\n") == 2 + 4
    rows = json.loads(render_matrix_json(report))
    assert rows[1]["remaining_issue"] and rows[0]["revised_location"]
    assert len(build_matrix(report)) == 4


def test_matrix_includes_sub_requirement_rows(tmp_path):
    b = DocxBuilder().heading("1 Intro", 1).para("Some text here about theory.")
    rep = audit_pair(
        tmp_path, b, b, make_issue(1, "Define the theory and explain its assumptions.", section="1")
    )
    ids = [r.id for r in build_matrix(rep)]
    assert ids == ["RT-001", "RT-001.1", "RT-001.2"]


def test_response_letter_never_says_addressed_unless_resolved(report):
    draft = generate_response(report)
    text = draft.text
    blocks = text.split("### Comment ")[1:]
    assert len(blocks) == 4
    for block, f in zip(blocks, report.findings, strict=True):
        says_addressed = "\nAddressed.\n" in block
        assert says_addressed == (f.status is St.RESOLVED)
        if f.status in (St.UNRESOLVED, St.PARTIALLY_RESOLVED):
            assert "Further revision required" in block
    assert draft.caveats == ["RT-001", "RT-002", "RT-003", "RT-004"]


def test_response_letter_is_grounded_in_findings(report):
    text = generate_response(report).text
    assert "Section 2.1 Theoretical Framework, paragraph 7" in text
    assert "Remove the repeated paragraph" in text
    for invented in ("has been added to Section 3.5", "psychometric"):
        assert invented not in text
    with_excerpts = generate_response(report, excerpts=True).text
    assert "Health Belief Model rests on six constructs" in with_excerpts


def test_response_letter_for_partial_compound(tmp_path):
    a = DocxBuilder().heading("1 Intro", 1).para("Stress affects adolescents in many ways.")
    b = (
        DocxBuilder()
        .heading("1 Intro", 1)
        .para("Stress affects adolescents in many ways.")
        .para(
            "The theory is defined here as a set of linked propositions about stress, appraisal and coping "
            "that together explain why some adolescents adapt well to strain and others do not."
        )
    )
    rep = audit_pair(
        tmp_path, a, b, make_issue(1, "Define the theory and explain its assumptions.", section="1")
    )
    text = generate_response(rep).text
    assert "By requirement" in text and "further revision required" in text
    assert "\nAddressed.\n" not in text


def test_terminal_output_shape_and_markup_safety(report, tmp_path):
    buf = StringIO()
    render_report(report, Console(file=buf, width=100, force_terminal=False, color_system=None))
    out = buf.getvalue()
    assert "REVIEWTRACE" in out and "Review issues:" in out and "RESOLVED" in out
    assert "UNRESOLVED" in out and "NEEDS REVIEW" in out and "Confidence: HIGH" in out
    assert "Evidence:" in out and "Remaining action:" in out

    b = DocxBuilder().heading("1 Intro", 1).para("Text.")
    hostile = make_issue(
        1, "[bold red]Remove[/bold red] \x1b[31mthis[/] [link=http://evil]x[/link]"
    )
    rep = audit_pair(tmp_path, b, b, hostile)
    buf = StringIO()
    render_report(rep, Console(file=buf, width=100, force_terminal=False, color_system=None))
    out = buf.getvalue()
    assert "[bold red]Remove[/bold red]" in out and "\x1b" not in out

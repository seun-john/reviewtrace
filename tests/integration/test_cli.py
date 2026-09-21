from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from reviewtrace import __version__
from reviewtrace.cli import app
from tests.fixtures import demo

runner = CliRunner()


@pytest.fixture()
def ex(demo_files):
    return demo_files


def run(*args):
    return runner.invoke(app, [str(a) for a in args])


def test_help_and_version():
    r = run("--help")
    assert r.exit_code == 0
    for cmd in ("comments", "extract", "diff", "audit", "response", "matrix", "inspect", "mcp"):
        assert cmd in r.output
    assert __version__ in run("--version").output


def test_comments_command(ex):
    r = run("comments", ex["reviewed"])
    assert r.exit_code == 0
    assert (
        "4 comment(s)" in r.output
        and "Anchored text" in r.output
        and "Explain the major" in r.output
    )
    data = json.loads(run("comments", ex["reviewed"], "--json").output)
    assert len(data) == 4 and data[0]["anchor_text"] == "The Health Belief Model"


def test_extract_from_comments_to_yaml_and_back_into_an_audit(ex, tmp_path):
    out = tmp_path / "issues.yml"
    r = run("extract", ex["reviewed"], "-o", out)
    assert r.exit_code == 0 and out.exists()
    text = out.read_text(encoding="utf-8")
    assert "RT-004" in text and "anchor:" in text and "paragraph: " in text
    audit = run(
        "audit",
        "--original",
        ex["original"],
        "--issues",
        out,
        "--revised",
        ex["revised"],
        "-f",
        "json",
    )
    assert audit.exit_code == 0
    statuses = [f["status"] for f in json.loads(audit.stdout)["findings"]]
    assert statuses == ["RESOLVED", "NEEDS_REVIEW", "NEEDS_REVIEW", "UNRESOLVED"]


def test_extract_from_report_to_stdout(ex, tmp_path):
    md = tmp_path / "r.md"
    md.write_text(demo.THESIS_REPORT_MD, encoding="utf-8")
    r = run("extract", md)
    assert (
        r.exit_code == 0 and r.stdout.startswith("version: 1") and r.stdout.count("- id: RT-") == 4
    )


def test_diff_command(ex):
    r = run("diff", ex["original"], ex["revised"])
    assert r.exit_code == 0
    assert "DOCUMENT DIFF" in r.output and "added" in r.output and "references" in r.output
    data = json.loads(run("diff", ex["original"], ex["revised"], "-f", "json").stdout)
    assert data["summary"]["references_added"] == 1


def test_audit_word_comments_terminal(ex):
    r = run("audit", ex["reviewed"], ex["revised"])
    assert r.exit_code == 0
    for needle in (
        "REVIEWTRACE",
        "Review issues:",
        "RT-001",
        "RT-004",
        "UNRESOLVED",
        "Confidence: HIGH",
    ):
        assert needle in r.output


def test_audit_three_file_workflow(ex, tmp_path):
    r = run(
        "audit",
        "--original",
        ex["original"],
        "--review",
        ex["report_docx"],
        "--revised",
        ex["revised"],
        "-f",
        "markdown",
    )
    assert r.exit_code == 0 and "## Traceability matrix" in r.stdout


def test_audit_writes_all_formats_and_response(ex, tmp_path):
    out = tmp_path / "out"
    r = run("audit", ex["reviewed"], ex["revised"], "--out-dir", out)
    assert r.exit_code == 0
    assert {p.name for p in out.iterdir()} == {
        "audit.json",
        "audit.md",
        "traceability.csv",
        "response.md",
    }
    letter = run("response", out / "audit.json")
    assert (
        letter.exit_code == 0
        and "### Comment 4" in letter.stdout
        and "Further revision required" in letter.stdout
    )
    matrix = run("matrix", out / "audit.json", "-f", "csv")
    assert matrix.stdout.splitlines()[0].startswith("ID,Reviewer,Comment")
    single = run("inspect", out / "audit.json", "rt-004")
    assert single.exit_code == 0 and "RT-004" in single.output and "UNRESOLVED" in single.output


def test_output_file_and_input_protection(ex, tmp_path):
    target = tmp_path / "report.json"
    assert run("audit", ex["reviewed"], ex["revised"], "-f", "json", "-o", target).exit_code == 0
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 1
    r = run("audit", ex["reviewed"], ex["revised"], "-f", "json", "-o", ex["reviewed"])
    assert r.exit_code == 1 and "refusing to overwrite" in r.output
    assert zipfile.is_zipfile(ex["reviewed"])  # still a valid docx


def test_fail_on(ex):
    assert run("audit", ex["reviewed"], ex["revised"], "--fail-on", "unresolved").exit_code == 3
    assert run("audit", ex["reviewed"], ex["revised"], "--fail-on", "not-resolved").exit_code == 3
    assert (
        run(
            "audit", ex["reviewed"], ex["reviewed"], "--fail-on", "unresolved", "-f", "json"
        ).exit_code
        == 3
    )


def test_usage_errors(ex):
    assert run("audit").exit_code == 2
    assert run("audit", ex["reviewed"]).exit_code == 2
    assert run("audit", "--original", ex["original"], "--revised", ex["revised"]).exit_code == 2
    both = run(
        "audit",
        "--original",
        ex["original"],
        "--review",
        "a.md",
        "--issues",
        "b.yml",
        "--revised",
        ex["revised"],
    )
    assert both.exit_code == 2
    assert (
        run("audit", ex["reviewed"], ex["revised"], "-o", "x.md").exit_code == 2
    )  # terminal + --output


def test_malformed_and_missing_files_give_clean_errors(ex, tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a docx")
    for args in (
        ("comments", bad),
        ("comments", tmp_path / "missing.docx"),
        ("diff", bad, ex["revised"]),
        ("audit", bad, ex["revised"]),
        ("audit", ex["reviewed"], tmp_path / "missing.docx"),
        ("extract", tmp_path / "missing.docx"),
        ("response", tmp_path / "missing.json"),
    ):
        r = run(*args)
        assert r.exit_code == 1, args
        assert "error:" in r.output and "Traceback" not in r.output, args


def test_unsupported_file_types(ex, tmp_path):
    doc = tmp_path / "old.doc"
    doc.write_bytes(b"x")
    r = run("comments", doc)
    assert r.exit_code == 1 and "unsupported file type" in r.output
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF")
    r = run("audit", "--original", ex["original"], "--review", pdf, "--revised", ex["revised"])
    assert r.exit_code == 1 and "unsupported" in r.output


def test_invalid_audit_json(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"schema_version": 1}', encoding="utf-8")
    r = run("response", p)
    assert r.exit_code == 1 and "invalid audit data" in r.output
    p.write_text("[]", encoding="utf-8")
    assert run("matrix", p).exit_code == 1


def test_unknown_issue_id(ex, tmp_path):
    out = tmp_path / "a.json"
    run("audit", ex["reviewed"], ex["revised"], "-f", "json", "-o", out)
    r = run("inspect", out, "RT-999")
    assert r.exit_code == 1 and "known: RT-001" in r.output


def test_docx_without_comments_is_reported_helpfully(tmp_path, ex):
    r = run("audit", ex["original"], ex["revised"])
    assert r.exit_code == 1 and "no Word comments" in r.output


def test_readme_quick_start_commands_run_against_the_committed_examples():
    root = Path(__file__).resolve().parents[2] / "examples" / "thesis"
    if not root.exists():
        pytest.skip("examples not generated")
    assert run("audit", root / "reviewed.docx", root / "revised.docx").exit_code == 0
    assert run("comments", root / "reviewed.docx").exit_code == 0
    assert run("diff", root / "original.docx", root / "revised.docx").exit_code == 0
    assert run("extract", root / "reviewer_comments.md").exit_code == 0
    assert (
        run(
            "audit",
            "--original",
            root / "original.docx",
            "--issues",
            root / "issues.yml",
            "--revised",
            root / "revised.docx",
        ).exit_code
        == 0
    )

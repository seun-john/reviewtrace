"""Revision guard: unrequested changes to protected wording, numbers and hedges."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from reviewtrace.cli import app
from reviewtrace.guard import GuardStatus, guard_revision
from reviewtrace.utils.errors import ReviewTraceError
from tests.fixtures.docx_builder import DocxBuilder

runner = CliRunner()


def make(path: Path, *paras: str, table: list[list[str]] | None = None) -> Path:
    builder = DocxBuilder()
    for text in paras:
        builder.para(text)
    if table:
        builder.table(table)  # type: ignore[arg-type]
    return builder.save(path)


def statuses(report) -> set[GuardStatus]:  # type: ignore[no-untyped-def]
    return {f.status for f in report.findings}


def test_identical_documents(tmp_path: Path) -> None:
    a = make(tmp_path / "a.docx", "Rates may rise by 12%.")
    b = make(tmp_path / "b.docx", "Rates may rise by 12%.")
    report = guard_revision(a, b)
    assert statuses(report) == {GuardStatus.UNCHANGED}
    assert not report.flagged


def test_number_formatting_is_not_a_change(tmp_path: Path) -> None:
    a = make(tmp_path / "a.docx", "Rates rose by 12.5% across 1,000 cases.")
    b = make(tmp_path / "b.docx", "Rates rose by 12.50% across 1000 cases.")
    assert GuardStatus.NUMBERS_CHANGED not in statuses(guard_revision(a, b))


def test_changed_number_is_flagged(tmp_path: Path) -> None:
    a = make(tmp_path / "a.docx", "Rates rose by 12% in 2020.")
    b = make(tmp_path / "b.docx", "Rates rose by 21% in 2020.")
    report = guard_revision(a, b)
    finding = next(f for f in report.findings if f.status is GuardStatus.NUMBERS_CHANGED)
    assert finding.evidence == {"removed": ["12%"], "added": ["21%"]}
    assert report.flagged


def test_removed_hedge_is_flagged(tmp_path: Path) -> None:
    a = make(tmp_path / "a.docx", "The drug may reduce risk.")
    b = make(tmp_path / "b.docx", "The drug reduces risk.")
    assert GuardStatus.HEDGE_REMOVED in statuses(guard_revision(a, b))


def test_protected_text_states(tmp_path: Path) -> None:
    a = make(tmp_path / "a.docx", "RQ1: Does X affect Y?", "RQ2: Does A affect B?")
    b = make(tmp_path / "b.docx", "RQ1: Does X influence Y?", "RQ2: Does A affect B?")
    report = guard_revision(
        a, b, ["RQ1: Does X affect Y?", "RQ2: Does A affect B?", "Never in the original"]
    )
    got = {f.evidence["text"]: f.status for f in report.findings if "text" in f.evidence}
    assert got["RQ1: Does X affect Y?"] is GuardStatus.PROTECTED_REMOVED
    assert got["RQ2: Does A affect B?"] is GuardStatus.PROTECTED_INTACT
    assert got["Never in the original"] is GuardStatus.INVALID_PROTECTION


def test_protected_match_ignores_whitespace_and_case(tmp_path: Path) -> None:
    a = make(tmp_path / "a.docx", "Approved   Title")
    b = make(tmp_path / "b.docx", "approved title")
    report = guard_revision(a, b, ["Approved Title"])
    assert GuardStatus.PROTECTED_INTACT in statuses(report)


def test_table_change(tmp_path: Path) -> None:
    a = make(tmp_path / "a.docx", "See table.", table=[["n", "5"]])
    b = make(tmp_path / "b.docx", "See table.", table=[["n", "6"]])
    got = statuses(guard_revision(a, b))
    assert GuardStatus.TABLE_CHANGED in got and GuardStatus.NUMBERS_CHANGED in got


def test_reworded_without_flags_needs_review(tmp_path: Path) -> None:
    a = make(tmp_path / "a.docx", "The sky is blue.")
    b = make(tmp_path / "b.docx", "The sky is a clear blue.")
    report = guard_revision(a, b)
    assert statuses(report) == {GuardStatus.NEEDS_REVIEW}
    assert not report.flagged


def test_blank_protected_phrase_is_an_error(tmp_path: Path) -> None:
    a = make(tmp_path / "a.docx", "x")
    with pytest.raises(ReviewTraceError):
        guard_revision(a, a, ["  "])


def test_inputs_are_not_modified(tmp_path: Path) -> None:
    a = make(tmp_path / "a.docx", "One 1.")
    b = make(tmp_path / "b.docx", "Two 2.")
    before = (a.read_bytes(), b.read_bytes())
    guard_revision(a, b)
    assert (a.read_bytes(), b.read_bytes()) == before


def test_cli_json_and_exit_code(tmp_path: Path) -> None:
    a = make(tmp_path / "a.docx", "Value 5.")
    b = make(tmp_path / "b.docx", "Value 6.")
    result = runner.invoke(app, ["guard", str(a), str(b), "-f", "json", "--fail-on-flag"])
    assert result.exit_code == 3
    assert json.loads(result.stdout)["findings"][0]["status"] == "NUMBERS_CHANGED"
    quiet = runner.invoke(app, ["guard", str(a), str(a)])
    assert quiet.exit_code == 0 and "UNCHANGED" in quiet.output

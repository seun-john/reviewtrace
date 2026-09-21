"""The demonstration from the README, end to end, in all three workflows."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from reviewtrace import core
from reviewtrace.models.finding import Confidence
from reviewtrace.models.finding import Status as St
from tests.fixtures import demo

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"

EXPECTED = [St.RESOLVED, St.NEEDS_REVIEW, St.NEEDS_REVIEW, St.UNRESOLVED]


def statuses(report):
    return [f.status for f in report.findings]


def sha(paths):
    return {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


def test_mode_a_word_comments(demo_files):
    r = core.audit_word_comments(demo_files["reviewed"], demo_files["revised"])
    assert statuses(r) == EXPECTED
    rt1, rt2, rt3, rt4 = r.findings
    assert rt1.confidence is Confidence.MEDIUM and rt1.supporting_evidence
    assert any(e.type.value == "tracked_revision" for e in rt1.evidence)
    assert "relates" in " ".join(rt2.missing_elements)
    assert any(e.type.value == "reference_added" for e in rt3.evidence)
    assert rt4.confidence is Confidence.HIGH and not rt4.requires_human_review
    assert "paragraphs 16–17" in " ".join(e.explanation for e in rt4.evidence)
    # Anchors are retained on the issues.
    assert r.issues[0].anchor_text == "The Health Belief Model"
    assert r.issues[3].anchor_text.startswith("Evidence from the region")
    assert [i.reviewer for i in r.issues] == [demo.SUPERVISOR] * 4


def test_mode_b_separate_review_report_md_and_docx(demo_files, tmp_path):
    md = tmp_path / "reviewer_comments.md"
    md.write_text(demo.THESIS_REPORT_MD, encoding="utf-8")
    for review in (md, demo_files["report_docx"]):
        r = core.audit_review_report(demo_files["original"], review, demo_files["revised"])
        assert statuses(r) == EXPECTED
        assert r.mode.value == "review_report"


def test_mode_c_issue_file(demo_files, tmp_path):
    issues = tmp_path / "issues.yml"
    issues.write_text(demo.THESIS_ISSUES_YML, encoding="utf-8")
    r = core.audit_issue_file(demo_files["original"], issues, demo_files["revised"])
    assert statuses(r) == EXPECTED


def test_peer_review_example(demo_files):
    root = demo_files["ms_original"].parent
    r = core.audit_review_report(
        demo_files["ms_original"], root / "reviewer_report.md", demo_files["ms_revised"]
    )
    assert statuses(r) == [St.RESOLVED] * 4
    assert [f.confidence for f in r.findings] == [
        Confidence.HIGH,
        Confidence.MEDIUM,
        Confidence.HIGH,
        Confidence.HIGH,
    ]
    assert [i.severity.value for i in r.issues] == ["major", "major", "major", "minor"]
    assert r.issues[0].reviewer == "Reviewer 1"


def test_business_example_covers_the_full_status_range(demo_files):
    root = demo_files["prop_original"].parent
    r = core.audit_issue_file(
        demo_files["prop_original"], root / "client_feedback.yml", demo_files["prop_revised"]
    )
    assert statuses(r) == [
        St.NEEDS_REVIEW,
        St.RESOLVED,
        St.RESOLVED,
        St.UNRESOLVED,
        St.NOT_ASSESSABLE,
    ]


def test_source_documents_are_never_modified(demo_files, tmp_path):
    paths = [
        demo_files["reviewed"],
        demo_files["revised"],
        demo_files["original"],
        demo_files["report_docx"],
    ]
    before = sha(paths)
    core.audit_word_comments(demo_files["reviewed"], demo_files["revised"])
    core.audit_review_report(
        demo_files["original"], demo_files["report_docx"], demo_files["revised"]
    )
    core.compare_documents(demo_files["original"], demo_files["revised"])
    core.extract_issues(demo_files["reviewed"])
    assert sha(paths) == before
    report = core.audit_word_comments(demo_files["reviewed"], demo_files["revised"])
    recorded = {i.role: i.sha256 for i in report.inputs}
    assert recorded["reviewed"] == before[str(demo_files["reviewed"])]


def test_no_network_and_no_api_key_needed(demo_files, monkeypatch):
    import socket

    def blocked(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert (
        statuses(core.audit_word_comments(demo_files["reviewed"], demo_files["revised"]))
        == EXPECTED
    )


def test_committed_examples_match_the_documented_results():
    """The files in examples/ are what the README commands run against."""
    thesis = EXAMPLES / "thesis"
    if not thesis.exists():
        pytest.skip("examples not generated")
    r = core.audit_word_comments(thesis / "reviewed.docx", thesis / "revised.docx")
    assert statuses(r) == EXPECTED
    r2 = core.audit_review_report(
        thesis / "original.docx", thesis / "reviewer_comments.md", thesis / "revised.docx"
    )
    assert statuses(r2) == EXPECTED
    r3 = core.audit_issue_file(
        thesis / "original.docx", thesis / "issues.yml", thesis / "revised.docx"
    )
    assert statuses(r3) == EXPECTED


def test_large_document_performance(tmp_path):
    """A 400-paragraph, 40-section thesis audits in seconds, not minutes."""
    import time

    from tests.fixtures.docx_builder import DocxBuilder

    def big(edit: bool) -> DocxBuilder:
        b = DocxBuilder()
        for s in range(40):
            b.heading(f"{s + 1} Section {s + 1} topic{s}", 1)
            for p in range(10):
                text = f"Section {s} paragraph {p} discusses subject{s}x{p} with detail alpha{p} beta{s} gamma."
                if edit and p == 3:
                    text += " An additional sentence was appended here."
                b.para(text)
        return b

    o, r = big(False).save(tmp_path / "o.docx"), big(True).save(tmp_path / "r.docx")
    t = time.perf_counter()
    d, _, _ = core.compare_documents(o, r)
    assert d.summary.paragraphs_modified == 40
    assert time.perf_counter() - t < 30

from __future__ import annotations

import pytest

from reviewtrace.core import run_audit
from reviewtrace.extractors.docx import load_document
from reviewtrace.models.evidence import Evidence, EvidenceDirection, EvidenceType
from reviewtrace.models.finding import (
    AssessmentSource,
    Confidence,
    Finding,
    SemanticAssessment,
    Status,
)
from reviewtrace.models.report import AuditMode
from reviewtrace.semantic import (
    DisabledSemanticReviewer,
    SemanticError,
    SemanticReviewer,
    build_payload,
    merge_assessment,
    parse_assessment,
)
from tests.fixtures.docx_builder import DocxBuilder
from tests.helpers import make_issue

ANCHOR = "This discussion is short."


def _pair(tmp_path):
    a = DocxBuilder().heading("1 Intro", 1).para(ANCHOR)
    b = (
        DocxBuilder()
        .heading("1 Intro", 1)
        .para(ANCHOR)
        .para("More words about the many aspects of the discussion at some length here. " * 5)
    )
    return load_document(a.save(tmp_path / "a.docx")), load_document(b.save(tmp_path / "b.docx"))


class Scripted(SemanticReviewer):
    name = "scripted"

    def __init__(self, status, refs, confidence=Confidence.HIGH):
        self._a = (status, refs, confidence)
        self.calls = []

    def assess(self, issue, original_context, revised_context, evidence):
        self.calls.append((issue.id, original_context, revised_context, len(evidence)))
        s, refs, c = self._a
        return SemanticAssessment(
            reviewer=self.name, status_recommendation=s, explanation="because", evidence_refs=refs,
            missing_requirements=["x"] if s is not Status.RESOLVED else [], confidence=c,
        )  # fmt: skip


def run(tmp_path, reviewer):
    o, r = _pair(tmp_path)
    issue = make_issue(1, "Expand this discussion.", ANCHOR)
    return run_audit(AuditMode.ISSUES_FILE, o, r, [issue], [], semantic=reviewer)


def test_disabled_is_the_default_and_never_called(tmp_path):
    o, r = _pair(tmp_path)
    rep = run_audit(
        AuditMode.ISSUES_FILE, o, r, [make_issue(1, "Expand this discussion.", ANCHOR)], []
    )
    assert rep.semantic_reviewer == "disabled"
    assert rep.findings[0].status is Status.NEEDS_REVIEW
    assert rep.findings[0].assessment_source is AssessmentSource.DETERMINISTIC
    with pytest.raises(SemanticError):
        DisabledSemanticReviewer().assess(make_issue(1, "x"), "", "", [])


def test_semantic_result_is_labelled_and_deterministic_status_kept(tmp_path):
    rev = Scripted(Status.RESOLVED, [0])
    f = run(tmp_path, rev).findings[0]
    assert f.assessment_source is AssessmentSource.SEMANTIC
    assert f.deterministic_status is Status.NEEDS_REVIEW
    assert f.status is Status.RESOLVED and f.confidence is Confidence.MEDIUM  # capped
    assert f.requires_human_review and f.supporting_evidence
    assert f.semantic.reviewer == "scripted" and rev.calls and rev.calls[0][1] == ANCHOR


def test_semantic_claim_without_cited_evidence_is_downgraded(tmp_path):
    f = run(tmp_path, Scripted(Status.RESOLVED, [])).findings[0]
    assert f.status is Status.NEEDS_REVIEW and "cited no evidence" in f.rationale
    f = run(tmp_path, Scripted(Status.RESOLVED, [99])).findings[0]
    assert f.status is Status.NEEDS_REVIEW


def test_low_confidence_semantic_resolved_is_not_resolved(tmp_path):
    f = run(tmp_path, Scripted(Status.RESOLVED, [0], Confidence.LOW)).findings[0]
    assert f.status is Status.NEEDS_REVIEW


def test_failing_reviewer_keeps_deterministic_result(tmp_path):
    class Broken(SemanticReviewer):
        name = "broken"

        def assess(self, *a):
            raise RuntimeError("network down")

    rep = run(tmp_path, Broken())
    assert rep.findings[0].status is Status.NEEDS_REVIEW
    assert rep.findings[0].assessment_source is AssessmentSource.DETERMINISTIC
    assert any("semantic review error" in w for w in rep.warnings)


def test_high_confidence_deterministic_result_is_not_overridden():
    ev = Evidence(
        type=EvidenceType.NO_CHANGE,
        direction=EvidenceDirection.CONTRADICTS,
        explanation="unchanged",
    )
    f = Finding(
        issue_id="RT-001",
        status=Status.UNRESOLVED,
        confidence=Confidence.HIGH,
        rationale="r",
        evidence=[ev],
    )
    a = SemanticAssessment(
        reviewer="x",
        status_recommendation=Status.RESOLVED,
        explanation="e",
        evidence_refs=[0],
        confidence=Confidence.HIGH,
    )
    merged = merge_assessment(f, a)
    assert merged.status is Status.UNRESOLVED and merged.semantic is a


def test_payload_is_structured_not_a_yes_no_question():
    ev = Evidence(
        type=EvidenceType.TEXT_ADDED,
        direction=EvidenceDirection.CONTEXT,
        explanation="added",
        revised_text="new text",
    )
    payload = build_payload(make_issue(1, "Expand this.", ANCHOR), "orig", "rev", [ev])
    assert {
        "reviewer_request",
        "original_context",
        "revised_context",
        "detected_changes",
        "response_schema",
    } <= payload.keys()
    assert payload["detected_changes"][0]["index"] == 0
    assert "evidence_refs" in payload["response_schema"]["properties"]
    assert "yes or no" not in payload["instructions"].lower()


def test_parse_assessment_validates_structure():
    ok = parse_assessment("r", '{"status_recommendation": "NEEDS_REVIEW", "explanation": "e"}')
    assert ok.reviewer == "r" and ok.confidence is Confidence.LOW
    for bad in (
        '{"status_recommendation": "MAYBE", "explanation": "e"}',
        "not json",
        '{"explanation": "e"}',
        "[]",
    ):
        with pytest.raises(SemanticError):
            parse_assessment("r", bad)

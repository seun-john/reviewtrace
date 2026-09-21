"""Status rules. The recurring question: could this ever produce a false RESOLVED?"""

from __future__ import annotations

import pytest

from reviewtrace import core
from reviewtrace.models.evidence import Evidence, EvidenceDirection, EvidenceType
from reviewtrace.models.finding import Confidence, Finding
from reviewtrace.models.finding import Status as St
from tests.fixtures.docx_builder import Anchor, DocxBuilder, Ins
from tests.helpers import audit_pair, make_issue, one

FILLER1 = "Duplicated filler sentence that appears in the draft twice over."
INTRO = "The study examines adolescent health behaviour in urban settings."
LIMITS_OLD = "The study was cross-sectional."


def doc(*paras: str, heading: str = "1 Introduction") -> DocxBuilder:
    b = DocxBuilder()
    b.heading(heading, 1)
    for p in paras:
        b.para(p)
    return b


def limitations(*extra: str) -> DocxBuilder:
    b = doc(INTRO)
    b.heading("2 Limitations", 1).para(LIMITS_OLD)
    for p in extra:
        b.para(p)
    return b


# --- removal -------------------------------------------------------------------------


def test_obvious_resolved_deletion(tmp_path):
    f = one(tmp_path, doc(INTRO, FILLER1), doc(INTRO), "Remove this paragraph.", anchor=FILLER1)
    assert f.status is St.RESOLVED and f.confidence is Confidence.HIGH
    assert f.supporting_evidence and f.supporting_evidence[0].type is EvidenceType.TEXT_REMOVED
    assert f.requires_human_review is False


def test_obvious_unresolved_instruction(tmp_path):
    f = one(
        tmp_path, doc(INTRO, FILLER1), doc(INTRO, FILLER1), "Remove this paragraph.", anchor=FILLER1
    )
    assert f.status is St.UNRESOLVED and f.confidence is Confidence.HIGH
    assert f.evidence[0].direction is EvidenceDirection.CONTRADICTS
    assert f.remaining_action


def test_rewritten_paragraph_is_not_counted_as_removed(tmp_path):
    reworded = "Duplicated filler sentence that appears in the draft twice."
    f = one(
        tmp_path,
        doc(INTRO, FILLER1),
        doc(INTRO, reworded),
        "Remove this paragraph.",
        anchor=FILLER1,
    )
    assert f.status is St.NEEDS_REVIEW


def test_moved_text_is_not_removed(tmp_path):
    a = doc(INTRO, FILLER1)
    b = doc(INTRO)
    b.heading("2 Elsewhere", 1).para(FILLER1)
    f = one(tmp_path, a, b, "Remove this paragraph.", anchor=FILLER1)
    assert f.status is St.UNRESOLVED


def test_removal_with_no_target_is_not_assessable(tmp_path):
    f = one(tmp_path, doc(INTRO), doc(INTRO), "Remove this.")
    assert f.status is St.NOT_ASSESSABLE


def test_duplicate_removed_in_section(tmp_path):
    a = doc("Adolescent health is shaped by many things.", FILLER1, FILLER1)
    b = doc("Adolescent health is shaped by many things.", FILLER1)
    f = one(tmp_path, a, b, "Remove the repeated paragraph in Section 1.")
    assert f.status is St.RESOLVED and f.confidence is Confidence.HIGH


def test_duplicate_still_present(tmp_path):
    a = doc("Adolescent health is shaped by many things.", FILLER1, FILLER1)
    f = one(tmp_path, a, a, "Remove the repeated paragraph in Section 1.")
    assert f.status is St.UNRESOLVED and "still" in f.rationale


def test_removed_tracked_deletion_is_added_as_evidence(tmp_path):
    a = doc(INTRO, FILLER1)
    b = DocxBuilder().heading("1 Introduction", 1).para(INTRO)
    b.para(__import__("tests.fixtures.docx_builder", fromlist=["Del"]).Del(FILLER1, author="Ann"))
    f = one(tmp_path, a, b, "Remove this paragraph.", anchor=FILLER1)
    assert f.status is St.RESOLVED
    assert any(e.type is EvidenceType.TRACKED_REVISION for e in f.evidence)


# --- additive requests ---------------------------------------------------------------

RECALL = "Self-reported data may be affected by recall bias because participants must remember past behaviour."


def test_add_request_resolved_only_with_new_text_containing_the_concept(tmp_path):
    f = one(
        tmp_path, limitations(), limitations(RECALL), "Add a limitation concerning recall bias."
    )
    assert f.status is St.RESOLVED and f.confidence is Confidence.MEDIUM
    assert f.method == "lexical-coverage" and f.requires_human_review
    assert "Limitations" in f.supporting_evidence[0].revised_location.label()


def test_unrelated_added_text_is_not_a_match(tmp_path):
    other = "Participants were recruited from three schools in the metropolitan area last year."
    f = one(tmp_path, limitations(), limitations(other), "Add a limitation concerning recall bias.")
    assert f.status is St.NEEDS_REVIEW and f.status is not St.RESOLVED


def test_text_added_elsewhere_is_not_resolved(tmp_path):
    a = limitations()
    b = doc(INTRO, RECALL)
    b.heading("2 Limitations", 1).para(LIMITS_OLD)
    f = one(tmp_path, a, b, "Add a limitation concerning recall bias.")
    assert f.status is St.NEEDS_REVIEW
    assert any(e.type is EvidenceType.TEXT_ADDED for e in f.evidence)


def test_nothing_added_is_unresolved(tmp_path):
    f = one(tmp_path, limitations(), limitations(), "Add a limitation concerning recall bias.")
    assert f.status is St.UNRESOLVED and f.confidence is Confidence.HIGH


def test_unlocated_request_never_reaches_resolved(tmp_path):
    a = doc(INTRO)
    b = doc(INTRO, RECALL)
    f = one(tmp_path, a, b, "Add something about recall bias in the discussion of participants.")
    assert f.status is not St.RESOLVED


def test_expand_is_structural_evidence_only(tmp_path):
    extra = ["More words here about the many aspects of the discussion at length. " * 6]
    a = doc("This discussion is short.")
    b = doc("This discussion is short.", *extra)
    f = one(tmp_path, a, b, "Expand this discussion.", anchor="This discussion is short.")
    assert f.status is St.NEEDS_REVIEW
    assert "semantic review" in f.rationale or "needs review" in f.rationale
    assert any("grew" in e.explanation or "new text" in e.explanation for e in f.evidence)


def test_ambiguous_explain_this_with_anchor(tmp_path):
    anchor = "A multistage sampling technique was adopted."
    a = doc(anchor)
    b = doc(
        anchor,
        "The sample was drawn in stages, first by district and then by household in each district.",
    )
    f = one(tmp_path, a, b, "Explain this.", anchor=anchor)
    assert f.status is St.NEEDS_REVIEW
    f2 = one(tmp_path, a, a, "Explain this.", anchor=anchor)
    assert f2.status is St.UNRESOLVED


def test_partial_resolution_names_what_is_missing(tmp_path):
    a = doc(INTRO)
    a.heading("3.5 Instruments", 1).para("The scale has twelve items.")
    b = doc(INTRO)
    b.heading("3.5 Instruments", 1).para("The scale has twelve items.")
    b.para(
        "Cronbach's alpha was 0.81, which indicates good internal reliability of the scale overall."
    )
    f = one(tmp_path, a, b, "Discuss the validity and reliability of the scale.", section="3.5")
    assert f.status is St.PARTIALLY_RESOLVED
    assert any("validity" in m for m in f.missing_elements)
    assert f.remaining_action is None or "validity" in f.remaining_action or f.missing_elements
    assert (
        f.supporting_evidence
        and "reliab" in f.supporting_evidence[0].explanation + f.supporting_evidence[0].revised_text
    )


def test_full_coverage_of_both_concepts_resolves(tmp_path):
    a = doc(INTRO)
    a.heading("3.5 Instruments", 1).para("The scale has twelve items.")
    b = doc(INTRO)
    b.heading("3.5 Instruments", 1).para("The scale has twelve items.")
    b.para(
        "Cronbach's alpha was 0.81, showing good reliability of the scale across the whole sample."
    )
    b.para(
        "Validity was supported by expert review and by a confirmatory factor analysis of the scale items."
    )
    f = one(tmp_path, a, b, "Discuss the validity and reliability of the scale.", section="3.5")
    assert f.status is St.RESOLVED


def test_subjective_comment_without_location_is_not_assessable(tmp_path):
    f = one(
        tmp_path,
        doc(INTRO),
        doc(INTRO, "Some other text was added to the document."),
        "Use a more convincing argument.",
    )
    assert f.status is St.NOT_ASSESSABLE and f.confidence is Confidence.LOW


def test_subjective_comment_with_location_and_changes_needs_review(tmp_path):
    f = one(
        tmp_path,
        doc(INTRO),
        doc("The study examines adolescent health in cities."),
        "Use a more convincing argument.",
        section="1",
    )
    assert f.status is St.NEEDS_REVIEW


def test_non_actionable_comment_is_not_assessable(tmp_path):
    f = one(tmp_path, doc(INTRO), doc(INTRO), "Good point.")
    assert f.status is St.NOT_ASSESSABLE


def test_vague_correct_this(tmp_path):
    a = doc(INTRO)
    assert one(tmp_path, a, a, "Correct this.", anchor=INTRO).status is St.UNRESOLVED
    b = doc("The study examines adolescent health behaviour in cities.")
    assert one(tmp_path, a, b, "Correct this.", anchor=INTRO).status is St.NEEDS_REVIEW
    assert one(tmp_path, a, b, "Correct this.").status is St.NOT_ASSESSABLE


# --- exact corrections -----------------------------------------------------------------


def test_numeric_correction_resolved_partial_unresolved(tmp_path):
    def d(*ps):
        return doc(*ps)

    old = "We surveyed 384 respondents in the main study."
    new = "We surveyed 396 respondents in the main study."
    f = one(tmp_path, d(old), d(new), "The sample size should be 396, not 384.", anchor=old)
    assert f.status is St.RESOLVED and f.confidence is Confidence.HIGH
    f = one(tmp_path, d(old), d(old), "The sample size should be 396, not 384.", anchor=old)
    assert f.status is St.UNRESOLVED
    both = [old, "A second mention says 384 respondents took part."]
    fixed_one = [new, "A second mention says 384 respondents took part."]
    f = one(tmp_path, d(*both), d(*fixed_one), "The sample size should be 396, not 384.")
    assert f.status is St.PARTIALLY_RESOLVED and any("384" in m for m in f.missing_elements)
    f = one(
        tmp_path,
        d(old),
        d("We surveyed a large number of respondents."),
        "The sample size should be 396, not 384.",
        anchor=old,
    )
    assert f.status is St.NEEDS_REVIEW


def test_number_boundaries_are_respected(tmp_path):
    a = doc("The fee was 3,840 dollars and 38 people paid it.")
    b = doc("The fee was 3,840 dollars and 38 people paid it.")
    f = one(tmp_path, a, b, "Change 384 to 396.")
    assert f.status is St.NEEDS_REVIEW  # 384 is not present as a number, so nothing can be verified


def test_heading_change_is_deterministic(tmp_path):
    def m(h):
        return DocxBuilder().heading(h, 1).para("Content about the approach taken.")

    f = one(
        tmp_path,
        m("2 Research Method"),
        m("2 Research Methodology"),
        "Change 'Research Method' to 'Research Methodology'.",
    )
    assert f.status is St.RESOLVED and f.confidence is Confidence.HIGH
    assert f.supporting_evidence[0].type is EvidenceType.HEADING_CHANGED
    f = one(
        tmp_path,
        m("2 Research Method"),
        m("2 Research Method"),
        "Change 'Research Method' to 'Research Methodology'.",
    )
    assert f.status is St.UNRESOLVED
    f = one(
        tmp_path,
        m("2 Research Method"),
        m("2 Approach"),
        "Change 'Research Method' to 'Research Methodology'.",
    )
    assert f.status is St.NEEDS_REVIEW


def test_text_replacement(tmp_path):
    a = doc("Respondents were adolescents. Adolescents were sampled at school.")
    b = doc("Respondents were teenagers. Teenagers were sampled at school.")
    f = one(tmp_path, a, b, "Replace 'adolescents' with 'teenagers'.")
    assert f.status is St.RESOLVED
    c = doc("Respondents were teenagers. Adolescents were sampled at school.")
    assert (
        one(tmp_path, a, c, "Replace 'adolescents' with 'teenagers'.").status
        is St.PARTIALLY_RESOLVED
    )


def test_table_correction(tmp_path):
    def t(v):
        b = DocxBuilder().heading("4 Results", 1).para("Table 4.2 Results by group")
        return b.table([["Group", "Pct"], ["A", v]])

    f = one(
        tmp_path,
        t("21%"),
        t("24%"),
        "Correct the percentage in Table 4.2; it should be 24%, not 21%.",
    )
    assert f.status is St.RESOLVED and f.confidence is Confidence.HIGH
    assert (
        one(
            tmp_path,
            t("21%"),
            t("21%"),
            "Correct the percentage in Table 4.2; it should be 24%, not 21%.",
        ).status
        is St.UNRESOLVED
    )
    unspecific = one(tmp_path, t("21%"), t("24%"), "Correct the percentage in Table 4.2.")
    assert unspecific.status is St.NEEDS_REVIEW  # changed, but no checkable target value
    assert (
        one(tmp_path, t("21%"), t("21%"), "Correct the percentage in Table 4.2.").status
        is St.UNRESOLVED
    )


def test_missing_table_is_not_assessable(tmp_path):
    f = one(tmp_path, doc(INTRO), doc(INTRO), "Correct the percentage in Table 9.9.")
    assert f.status is St.NOT_ASSESSABLE


# --- references ------------------------------------------------------------------------


def refs(*items: str) -> DocxBuilder:
    b = doc(INTRO)
    b.heading("References", 1)
    for i in items:
        b.para(i)
    return b


R_OLD = (
    "Adams, R. (2016). Peer influence and adolescent risk. Journal of Youth Studies, 12(3), 44-58."
)
R_NEW = "Okonkwo, C. (2024). Digital media and adolescent health. African Journal of Public Health, 19(1), 1-14."
R_NEW2 = "Bello, F. (2023). Peer norms online. Youth and Society, 5(2), 3-9."


def test_reference_requests_without_criteria_never_resolve(tmp_path):
    f = one(tmp_path, refs(R_OLD), refs(R_OLD, R_NEW), "Add recent references.")
    assert f.status is St.NEEDS_REVIEW and any(
        e.type is EvidenceType.REFERENCE_ADDED for e in f.evidence
    )
    assert one(tmp_path, refs(R_OLD), refs(R_OLD), "Add recent references.").status is St.UNRESOLVED


def test_reference_criteria_are_explicit(tmp_path):
    q = "Add at least two references published since 2020."
    assert one(tmp_path, refs(R_OLD), refs(R_OLD, R_NEW, R_NEW2), q).status is St.RESOLVED
    f = one(tmp_path, refs(R_OLD), refs(R_OLD, R_NEW), q)
    assert f.status is St.PARTIALLY_RESOLVED and f.missing_elements
    assert (
        one(tmp_path, refs(R_OLD), refs(R_OLD, "Old, A. (2010). Old paper. Journal."), q).status
        is St.UNRESOLVED
    )


def test_named_citation(tmp_path):
    a = refs(R_OLD)
    with_ref = refs(R_OLD, R_NEW)
    assert one(tmp_path, a, with_ref, "Cite Okonkwo (2024).").status is St.PARTIALLY_RESOLVED
    b = doc(INTRO, "Digital media matter (Okonkwo, 2024).")
    b.heading("References", 1).para(R_OLD)
    b.para(R_NEW)
    assert one(tmp_path, a, b, "Cite Okonkwo (2024).").status is St.RESOLVED
    assert one(tmp_path, a, a, "Cite Okonkwo (2024).").status is St.UNRESOLVED


def test_citation_style_is_not_assessable(tmp_path):
    f = one(tmp_path, refs(R_OLD), refs(R_OLD), "Ensure all references use APA 7th.")
    assert f.status is St.NOT_ASSESSABLE and "APA" in f.rationale


# --- moves, structure ----------------------------------------------------------------


def test_move_paragraph(tmp_path):
    a = doc(INTRO, FILLER1)
    a.heading("3 Timeline", 1).para("Delivery takes twelve weeks.")
    b = doc(INTRO)
    b.heading("3 Timeline", 1).para("Delivery takes twelve weeks.")
    b.para(FILLER1)
    assert (
        one(tmp_path, a, b, "Move this paragraph to Section 3.", anchor=FILLER1).status
        is St.RESOLVED
    )
    assert (
        one(tmp_path, a, a, "Move this paragraph to Section 3.", anchor=FILLER1).status
        is St.UNRESOLVED
    )


# --- compound comments -----------------------------------------------------------------


def test_compound_is_never_resolved_when_only_one_part_is(tmp_path):
    a = doc("Stress affects adolescents in many ways.")
    theory = (
        "The theory is defined here as a set of linked propositions about stress, appraisal and coping "
        "that together explain why some adolescents adapt well to strain and others do not."
    )
    b = doc("Stress affects adolescents in many ways.", theory)
    f = one(tmp_path, a, b, "Define the theory and explain its assumptions.", section="1")
    assert f.status is not St.RESOLVED
    assert [s.status for s in f.sub_findings] == [St.RESOLVED, St.NEEDS_REVIEW]
    assert f.confidence in (Confidence.MEDIUM, Confidence.LOW)
    assert any("assumptions" in m.lower() for m in f.missing_elements)


def test_compound_resolves_only_when_all_parts_do(tmp_path):
    a = doc("Stress affects adolescents in many ways.")
    both = (
        "The theory is defined here as a set of linked propositions about stress, appraisal and coping. "
        "Its assumptions are that appraisal is subjective, that coping is learned and that strain accumulates "
        "over time in ways that shape adolescent adjustment."
    )
    b = doc("Stress affects adolescents in many ways.", both)
    f = one(tmp_path, a, b, "Define the theory and explain its assumptions.", section="1")
    assert [s.status for s in f.sub_findings] == [St.RESOLVED, St.RESOLVED]
    assert f.status is St.RESOLVED


def test_compound_partial_when_one_part_is_clearly_unresolved(tmp_path):
    a = doc(INTRO, FILLER1)
    f = one(tmp_path, a, doc(INTRO), "Remove the first paragraph and add a table.", anchor=FILLER1)
    assert f.status is not St.RESOLVED


# --- invariants, errors, semantic ------------------------------------------------------


def test_resolved_finding_requires_supporting_evidence():
    with pytest.raises(ValueError, match="supporting evidence"):
        Finding(issue_id="RT-001", status=St.RESOLVED, confidence=Confidence.HIGH, rationale="x")
    with pytest.raises(ValueError, match="supporting evidence"):
        Finding(
            issue_id="RT-001",
            status=St.RESOLVED,
            confidence=Confidence.HIGH,
            rationale="x",
            evidence=[
                Evidence(
                    type=EvidenceType.NO_CHANGE,
                    direction=EvidenceDirection.CONTRADICTS,
                    explanation="e",
                )
            ],
        )
    ok = Evidence(
        type=EvidenceType.TEXT_ADDED, direction=EvidenceDirection.SUPPORTS, explanation="e"
    )
    with pytest.raises(ValueError, match="LOW"):
        Finding(
            issue_id="RT-001",
            status=St.RESOLVED,
            confidence=Confidence.LOW,
            rationale="x",
            evidence=[ok],
        )


def test_every_resolved_finding_in_the_demo_has_evidence(demo_files):
    report = core.audit_word_comments(demo_files["reviewed"], demo_files["revised"])
    for f in report.findings:
        if f.status is St.RESOLVED:
            assert f.supporting_evidence


def test_an_exception_becomes_an_error_finding(tmp_path, monkeypatch):
    def boom(issue, ctx):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(core, "assess_issue", boom)
    report = audit_pair(
        tmp_path,
        doc(INTRO),
        doc(INTRO),
        make_issue(1, "Add a table."),
        make_issue(2, "Remove this."),
    )
    assert [f.status for f in report.findings] == [St.ERROR, St.ERROR]
    assert "kaboom" in report.findings[0].rationale and report.findings[0].requires_human_review


def test_word_resolved_flag_never_decides_status(tmp_path):
    def reviewed(done):
        b = DocxBuilder().heading("1 Introduction", 1).para(INTRO, Anchor(1, FILLER1))
        return b.comment(1, "Remove this sentence.", done=done)

    revised_unchanged = DocxBuilder().heading("1 Introduction", 1).para(INTRO + FILLER1)
    p = reviewed(True).save(tmp_path / "rev.docx")
    q = revised_unchanged.save(tmp_path / "new.docx")
    report = core.audit_word_comments(p, q)
    f = report.findings[0]
    assert f.word_comment_resolved is True and f.status is St.UNRESOLVED

    revised_fixed = DocxBuilder().heading("1 Introduction", 1).para(INTRO)
    q2 = revised_fixed.save(tmp_path / "new2.docx")
    p2 = reviewed(False).save(tmp_path / "rev2.docx")
    f2 = core.audit_word_comments(p2, q2).findings[0]
    assert f2.word_comment_resolved is False and f2.status is St.RESOLVED


def test_tracked_insertion_becomes_evidence(tmp_path):
    a = doc("The scale has twelve items.")
    b = DocxBuilder().heading("1 Introduction", 1).para("The scale has twelve items.")
    b.para(
        Ins(
            "Cronbach's alpha coefficient of reliability was 0.86 across all twelve scale items in the pilot.",
            author="Ann",
        )
    )
    f = one(tmp_path, a, b, "Add the reliability coefficient.", section="1")
    assert f.status is St.RESOLVED
    assert any(e.type is EvidenceType.TRACKED_REVISION for e in f.evidence)


def test_semantic_is_disabled_by_default(tmp_path):
    report = audit_pair(
        tmp_path, doc(INTRO), doc(INTRO), make_issue(1, "Expand this discussion.", INTRO)
    )
    assert report.semantic_reviewer == "disabled"
    assert all(
        f.assessment_source.value == "deterministic" and f.semantic is None for f in report.findings
    )


def test_table_correction_reports_the_table_as_changed(tmp_path):
    def t(v):
        b = DocxBuilder().heading("4 Results", 1).para("Table 4.2 Results by group")
        return b.table([["Group", "Pct"], ["A", v]])

    assert (
        one(
            tmp_path, t("21%"), t("24%"), "Correct Table 4.2; it should be 24%, not 21%."
        ).document_changed
        is True
    )
    assert (
        one(
            tmp_path, t("21%"), t("21%"), "Correct Table 4.2; it should be 24%, not 21%."
        ).document_changed
        is False
    )

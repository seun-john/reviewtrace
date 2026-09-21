"""Demonstration documents: a thesis chapter, a journal manuscript and a client proposal."""

from __future__ import annotations

from pathlib import Path

from tests.fixtures.docx_builder import Anchor, DocxBuilder, Ins

SUPERVISOR = "Prof. Adaeze Okafor"
CONSTRUCTS = (
    "The Health Belief Model rests on six constructs. Perceived susceptibility is an individual's belief "
    "about the likelihood of developing a health problem, and perceived severity is the belief about how "
    "serious that problem would be. Perceived benefits and perceived barriers describe the expected gains "
    "and costs of taking action, cues to action are the triggers that prompt behaviour, and self-efficacy "
    "is confidence in one's ability to act."
)
REPEATED = "Evidence from the region is mixed and the empirical base remains limited, which motivates the present study."
REF_ADAMS = (
    "Adams, R. (2016). Peer influence and adolescent risk. Journal of Youth Studies, 12(3), 44-58."
)
REF_BROWN = "Brown, T. (2014). Health behaviour in context. Public Health Review, 8(2), 11-25."
REF_NEW = "Okonkwo, C., & Bello, F. (2024). Digital media and adolescent health choices. African Journal of Public Health, 19(1), 1-14."

THESIS_COMMENTS = [
    "Explain the major constructs of the Health Belief Model.",
    "Show how the model relates to the present study.",
    "Add recent references.",
    "Remove the repeated paragraph in Section 2.4.",
]


def thesis_original(with_comments: bool) -> DocxBuilder:
    def a(cid: int, text: str) -> str | Anchor:
        return Anchor(cid, text) if with_comments else text

    b = DocxBuilder(title="Adolescent Health Behaviour in Lagos State")
    b.title_para("Adolescent Health Behaviour in Lagos State")
    b.heading("1 Introduction", 1)
    b.para("Adolescent health behaviour is a growing public health concern in Nigeria.")
    b.heading("2 Literature Review", 1)
    b.heading("2.1 Theoretical Framework", 2)
    b.para(a(1, "The Health Belief Model"), " ", a(2, "was used in this study."))
    b.heading("2.2 Empirical Review", 2)
    b.para(
        a(3, "Several studies have examined adolescent health behaviour in low-resource settings."),
        " Peer influence is consistently linked to risk-taking (Adams, 2016).",
    )
    b.para("Family structure and parental monitoring also shape adolescent outcomes (Brown, 2014).")
    b.heading("2.3 Conceptual Framework", 2)
    b.para(
        "The study conceptualises health behaviour as a function of beliefs, social context and access to services."
    )
    b.heading("2.4 Summary of the Literature", 2)
    b.para(
        "Adolescent health behaviour is shaped by individual beliefs, peers and the wider environment."
    )
    b.para(REPEATED)
    b.para(a(4, REPEATED))
    b.heading("3 Methodology", 1)
    b.heading("3.1 Research Design", 2)
    b.para("A cross-sectional survey design was used.")
    b.heading("References", 1)
    b.para(REF_ADAMS)
    b.para(REF_BROWN)
    if with_comments:
        for cid, text in enumerate(THESIS_COMMENTS, start=1):
            b.comment(
                cid, text, author=SUPERVISOR, initials="AO", date=f"2024-04-{cid:02d}T09:00:00Z"
            )
    return b


def thesis_revised() -> DocxBuilder:
    """Fully addresses comment 1, only loosely comment 2, adds one reference, keeps the duplicate."""
    b = DocxBuilder(title="Adolescent Health Behaviour in Lagos State")
    b.title_para("Adolescent Health Behaviour in Lagos State")
    b.heading("1 Introduction", 1)
    b.para("Adolescent health behaviour is a growing public health concern in Nigeria.")
    b.heading("2 Literature Review", 1)
    b.heading("2.1 Theoretical Framework", 2)
    b.para("The Health Belief Model was used in this study.")
    b.para(Ins(CONSTRUCTS, author="A. Student", date="2024-05-12T14:30:00Z"))
    b.para(
        "The model is widely applied in health promotion research, including work with young people."
    )
    b.heading("2.2 Empirical Review", 2)
    b.para(
        "Several studies have examined adolescent health behaviour in low-resource settings."
        " Peer influence is consistently linked to risk-taking (Adams, 2016)."
    )
    b.para("Family structure and parental monitoring also shape adolescent outcomes (Brown, 2014).")
    b.heading("2.3 Conceptual Framework", 2)
    b.para(
        "The study conceptualises health behaviour as a function of beliefs, social context and access to services."
    )
    b.heading("2.4 Summary of the Literature", 2)
    b.para(
        "Adolescent health behaviour is shaped by individual beliefs, peers and the wider environment."
    )
    b.para(REPEATED)
    b.para(REPEATED)
    b.heading("3 Methodology", 1)
    b.heading("3.1 Research Design", 2)
    b.para("A cross-sectional survey design was used.")
    b.heading("References", 1)
    b.para(REF_ADAMS)
    b.para(REF_BROWN)
    b.para(REF_NEW)
    return b


THESIS_REPORT_MD = """# Supervisor comments

1. Explain the major constructs of the Health Belief Model.
2. Show how the model relates to the present study.
3. Add recent references.
4. Remove the repeated paragraph in Section 2.4.
"""

THESIS_ISSUES_YML = """version: 1
issues:
  - id: RT-001
    reviewer: Prof. Adaeze Okafor
    comment: Explain the major constructs of the Health Belief Model.
    anchor: The Health Belief Model was used in this study.
    location:
      section: "2.1"
    category: EXPLAIN
    severity: major
  - id: RT-002
    reviewer: Prof. Adaeze Okafor
    comment: Show how the model relates to the present study.
    location:
      section: "2.1"
    category: EXPLAIN
  - id: RT-003
    reviewer: Prof. Adaeze Okafor
    comment: Add recent references.
    category: REFERENCE
  - id: RT-004
    reviewer: Prof. Adaeze Okafor
    comment: Remove the repeated paragraph in Section 2.4.
    location:
      section: "2.4"
    category: REMOVE
"""


def thesis_report_docx() -> DocxBuilder:
    b = DocxBuilder()
    b.heading("Supervisor comments", 1)
    for text in THESIS_COMMENTS:
        b.para(text, numbered=True)
    return b


# --- journal manuscript ------------------------------------------------------------

SAMPLE_ORIG = "A sample of 384 respondents was selected using multistage sampling."
SAMPLE_REV = "A sample of 396 respondents was selected using multistage sampling."
RECALL = "Self-reported data may be affected by recall bias, because participants were asked to remember past behaviour."

MANUSCRIPT_REPORT_MD = """# Reviewer 1

## Major comments

1. The sample size should be 396, not 384.
2. Add a limitation concerning recall bias.
3. Correct the prevalence for the 30-44 age group in Table 3.1; it should be 24%, not 21%.

## Minor comments

4. Change 'Methods' to 'Materials and Methods'.
"""


def manuscript(revised: bool) -> DocxBuilder:
    b = DocxBuilder(title="Prevalence of Hypertension in Urban Adults")
    b.heading("1 Introduction", 1)
    b.para("Hypertension is a leading risk factor for cardiovascular disease.")
    b.heading("2 Materials and Methods" if revised else "2 Methods", 1)
    b.heading("2.1 Sample Size", 2)
    b.para(SAMPLE_REV if revised else SAMPLE_ORIG)
    b.heading("3 Results", 1)
    b.para("Table 3.1 Prevalence of hypertension by age group")
    b.table(
        [
            ["Age group", "Prevalence"],
            ["18-29", "12%"],
            ["30-44", "24%" if revised else "21%"],
            ["45+", "38%"],
        ]
    )
    b.heading("4 Discussion", 1)
    b.para("Prevalence increased with age across the sample.")
    b.heading("4.2 Limitations", 2)
    b.para("The study was cross-sectional.")
    if revised:
        b.para(RECALL)
    b.heading("References", 1)
    b.para("World Health Organization. (2021). Hypertension fact sheet. WHO Press.")
    return b


# --- client proposal ---------------------------------------------------------------

PROPOSAL_ISSUES_YML = """version: 1
issues:
  - id: RT-001
    reviewer: Client
    comment: Clarify the payment schedule.
    anchor: Payment is due on delivery.
    category: CLARIFY
  - id: RT-002
    reviewer: Client
    comment: Remove the paragraph about optional training.
    anchor: Optional training can be arranged on request.
    category: REMOVE
  - id: RT-003
    reviewer: Client
    comment: Move the risks paragraph to Section 3.
    anchor: The main delivery risk is late access to client data.
    category: MOVE
  - id: RT-004
    reviewer: Client
    comment: Use a more convincing value proposition.
    location:
      section: "1"
  - id: RT-005
    reviewer: Client
    comment: Make the overall tone more professional.
"""


def proposal(revised: bool) -> DocxBuilder:
    b = DocxBuilder(title="Analytics Platform Proposal")
    b.heading("1 Scope", 1)
    b.para("We will build a reporting platform for the client's sales team.")
    b.para("The platform will save the sales team time.")
    b.heading("2 Pricing", 1)
    b.para("Payment is due on delivery.")
    if revised:
        b.para("Invoices are issued at delivery and are payable within 14 days.")
    if not revised:
        b.para("Optional training can be arranged on request.")
        b.para("The main delivery risk is late access to client data.")
    b.heading("3 Timeline", 1)
    b.para("Delivery is planned for twelve weeks from kickoff.")
    if revised:
        b.para("The main delivery risk is late access to client data.")
    return b


def write_all(root: Path) -> dict[str, Path]:
    """Write every example file under ``root`` (used by scripts/build_examples.py)."""
    out: dict[str, Path] = {}
    thesis = root / "thesis"
    out["reviewed"] = thesis_original(True).save(thesis / "reviewed.docx")
    out["original"] = thesis_original(False).save(thesis / "original.docx")
    out["revised"] = thesis_revised().save(thesis / "revised.docx")
    out["report_docx"] = thesis_report_docx().save(thesis / "reviewer_comments.docx")
    (thesis / "reviewer_comments.md").write_text(THESIS_REPORT_MD, encoding="utf-8")
    (thesis / "issues.yml").write_text(THESIS_ISSUES_YML, encoding="utf-8")
    pr = root / "peer_review"
    out["ms_original"] = manuscript(False).save(pr / "manuscript_original.docx")
    out["ms_revised"] = manuscript(True).save(pr / "manuscript_revised.docx")
    (pr / "reviewer_report.md").write_text(MANUSCRIPT_REPORT_MD, encoding="utf-8")
    biz = root / "business"
    out["prop_original"] = proposal(False).save(biz / "proposal_original.docx")
    out["prop_revised"] = proposal(True).save(biz / "proposal_revised.docx")
    (biz / "client_feedback.yml").write_text(PROPOSAL_ISSUES_YML, encoding="utf-8")
    return out

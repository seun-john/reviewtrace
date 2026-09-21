from __future__ import annotations

import pytest

from reviewtrace.analysis.classifier import classify, decompose
from reviewtrace.extractors.issues_file import issues_to_yaml, load_issues_file
from reviewtrace.extractors.reviewer_report import extract_report
from reviewtrace.models.issue import IssueCategory as C
from reviewtrace.utils.errors import IssueFileError, UnsupportedFileTypeError
from tests.fixtures.docx_builder import DocxBuilder


def write(tmp_path, text, name="r.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_numbered_comments(tmp_path):
    text = (
        "1. Expand the background section.\n"
        "2. Explain the sampling procedure.\n"
        "3. Correct Table 4.3.\n"
    )
    res = extract_report(write(tmp_path, text))
    assert [i.id for i in res.issues] == ["RT-001", "RT-002", "RT-003"]
    assert [i.category for i in res.issues] == [C.EXPAND, C.EXPLAIN, C.TABLE]
    assert res.issues[0].source_id == "1"


def test_bullet_comments_and_wrapped_lines(tmp_path):
    text = (
        "- Update references to include recent literature.\n"
        "- Remove the duplicated paragraph\n  in the introduction.\n"
    )
    res = extract_report(write(tmp_path, text))
    assert len(res.issues) == 2
    assert res.issues[1].comment_text == "Remove the duplicated paragraph in the introduction."
    assert res.issues[0].category is C.REFERENCE


def test_prose_report_extracts_requests_and_skips_praise(tmp_path):
    text = (
        "This is an interesting study. The authors should explain how the sample was chosen. "
        "Please add a limitations section. The topic is timely.\n"
    )
    res = extract_report(write(tmp_path, text))
    assert len(res.issues) == 2
    assert res.ignored_sentences == 2
    assert any("not extracted" in w for w in res.warnings)


def test_severity_and_reviewer_from_headings(tmp_path):
    text = (
        "# Reviewer 2\n\n## Major comments\n\n1. Justify the sample size.\n\n"
        "## Minor comments\n\n2. Fix the typo in the abstract.\n"
    )
    res = extract_report(write(tmp_path, text))
    assert res.issues[0].reviewer == "Reviewer 2"
    assert res.issues[0].severity.value == "major"
    assert res.issues[1].severity.value == "minor"


def test_quoted_anchor_and_section_hint(tmp_path):
    text = (
        '1. In Section 3.3, "A sample of 396 respondents was selected." '
        "Explain how this was calculated.\n2. Second comment here.\n"
    )
    i = extract_report(write(tmp_path, text)).issues[0]
    assert i.anchor_text == "A sample of 396 respondents was selected."
    assert i.anchor_location.section == "3.3"


def test_markdown_table_report(tmp_path):
    text = (
        "| No. | Comment | Section |\n| --- | --- | --- |\n"
        "| 1 | Explain the sampling procedure. | 3.4 |\n| 2 | Add a limitation. | 5 |\n"
    )
    res = extract_report(write(tmp_path, text))
    assert [i.comment_text for i in res.issues] == [
        "Explain the sampling procedure.",
        "Add a limitation.",
    ]
    assert res.issues[0].anchor_location.section == "3.4"


def test_docx_report_with_auto_numbering(tmp_path):
    b = DocxBuilder()
    b.heading("Supervisor comments", 1)
    b.para("Expand the background section.", numbered=True)
    b.para("Explain the sampling procedure", numbered=True)  # no full stop: not a heading
    res = extract_report(b.save(tmp_path / "r.docx"))
    assert [i.comment_text for i in res.issues] == [
        "Expand the background section.",
        "Explain the sampling procedure",
    ]


def test_unsupported_report_type(tmp_path):
    p = tmp_path / "r.pdf"
    p.write_bytes(b"%PDF")
    with pytest.raises(UnsupportedFileTypeError):
        extract_report(p)


# --- compound comments ---------------------------------------------------------------


def test_compound_comment_is_split():
    parts = decompose(
        "Define the theory, explain its assumptions and show how it relates to this study."
    )
    assert parts == [
        "Define the theory",
        "Explain its assumptions",
        "Show how it relates to this study",
    ]


def test_bare_verb_borrows_object():
    assert decompose("Explain and justify the sample size.") == [
        "Explain the sample size",
        "Justify the sample size",
    ]


def test_coordinated_nouns_are_not_split():
    assert decompose("Discuss the validity and reliability of the scale.") == []


def test_compound_issue_gets_sub_ids(tmp_path):
    text = (
        "14. Define the theory, explain its assumptions and show how it relates to this study.\n"
        "15. Add a note.\n"
    )
    issue = extract_report(write(tmp_path, text)).issues[0]
    assert issue.is_compound
    assert [s.id for s in issue.sub_requirements] == ["RT-001.1", "RT-001.2", "RT-001.3"]


# --- classifier ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Remove this duplicated paragraph.", C.REMOVE),
        ("Change 'Research Method' to 'Research Methodology'.", C.REPLACE),
        ("The sample size should be 396, not 384.", C.CORRECT),
        ("Add recent references.", C.REFERENCE),
        ("Correct the percentage in Table 4.2.", C.TABLE),
        ("Expand this discussion.", C.EXPAND),
        ("Move this paragraph to Section 3.2.", C.MOVE),
        ("Ensure all references use APA 7th.", C.REFORMAT),
        ("Show how the model relates to the present study.", C.EXPLAIN),
        ("How was the sample size calculated?", C.EXPLAIN),
    ],
)
def test_categories(text, category):
    assert classify(text).category is category


def test_non_actionable_and_subjective():
    assert classify("Good point.").actionable is False
    c = classify("Use a more convincing argument.")
    assert c.subjective and c.category is C.OTHER


def test_classifier_survives_pathological_input():
    assert classify("a" * 200_000).category is C.OTHER
    assert decompose("and, " * 50_000) == []


# --- issues.yml ----------------------------------------------------------------------


def test_issues_yaml_round_trip(tmp_path):
    text = "1. Explain the sampling procedure.\n2. Remove the repeated paragraph in Section 2.4.\n"
    issues = extract_report(write(tmp_path, text)).issues
    out = tmp_path / "issues.yml"
    out.write_text(issues_to_yaml(issues), encoding="utf-8")
    again = load_issues_file(out)
    assert [(i.id, i.comment_text, i.category) for i in again] == [
        (i.id, i.comment_text, i.category) for i in issues
    ]


def test_issues_file_example_from_spec(tmp_path):
    body = (
        "version: 1\nissues:\n  - id: RT-001\n    reviewer: Supervisor\n    comment: >\n"
        "      Explain how the sample size was calculated.\n    anchor: >\n"
        "      The study included 396 respondents.\n    location:\n      section: Sample Size\n"
        "    category: EXPLAIN\n    severity: major\n"
    )
    (i,) = load_issues_file(write(tmp_path, body, "issues.yml"))
    assert i.comment_text == "Explain how the sample size was calculated."
    assert i.anchor_text == "The study included 396 respondents."
    assert i.anchor_location.section == "Sample Size" and i.severity.value == "major"


@pytest.mark.parametrize(
    "body",
    [
        "version: 1\nissues:\n  - comments: typo in key\n",
        "version: 2\nissues: []\n",
        "version: 1\nissues:\n  - id: A\n    comment: x\n  - id: A\n    comment: y\n",
        "version: 1\nissues:\n  - comment: '  '\n",
        "just a string",
        "version: 1\nissues:\n  - comment: x\n    category: NOPE\n",
        "key: [unclosed",
    ],
)
def test_invalid_issue_files_are_rejected(tmp_path, body):
    with pytest.raises(IssueFileError):
        load_issues_file(write(tmp_path, body, "issues.yml"))


def test_yaml_cannot_execute_python(tmp_path):
    body = "version: 1\nissues:\n  - comment: !!python/object/apply:os.system ['echo pwned']\n"
    with pytest.raises(IssueFileError):
        load_issues_file(write(tmp_path, body, "issues.yml"))


def test_duplicate_comments_are_flagged_not_merged(tmp_path):
    text = (
        "1. Explain the sampling procedure.\n2. Please explain the sampling procedure.\n"
        "3. Add a table.\n"
    )
    a, b, c = extract_report(write(tmp_path, text)).issues
    assert a.possible_duplicates == [b.id] and b.possible_duplicates == [a.id]
    assert c.possible_duplicates == []


def test_labelled_comments(tmp_path):
    text = "Comment 1: Explain the sampling procedure.\nComment 2: Add a limitations section.\n"
    res = extract_report(write(tmp_path, text))
    assert [i.comment_text for i in res.issues] == [
        "Explain the sampling procedure.",
        "Add a limitations section.",
    ]
    assert [i.source_id for i in res.issues] == ["1", "2"]

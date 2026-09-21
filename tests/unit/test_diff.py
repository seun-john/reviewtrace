from __future__ import annotations

from reviewtrace.core import compare_documents
from reviewtrace.models.diff import ChangeKind as K
from reviewtrace.models.diff import SectionMatchKind, TableChangeKind
from tests.fixtures.docx_builder import DocxBuilder

P1 = "Intro paragraph one is about health and behaviour in communities."
P2 = "Sampling paragraph about the selection of respondents in the field."
P3 = "We surveyed 384 respondents across three districts using questionnaires."


def diff(tmp_path, a: DocxBuilder, b: DocxBuilder):
    d, _o, _r = compare_documents(a.save(tmp_path / "a.docx"), b.save(tmp_path / "b.docx"))
    return d


def kinds(d):
    return [c.kind for c in d.paragraphs if not c.is_heading]


def two_sections(intro, method, method_heading="2 Method"):
    b = DocxBuilder()
    b.heading("1 Introduction", 1)
    for p in intro:
        b.para(p)
    b.heading(method_heading, 1)
    for p in method:
        b.para(p)
    return b


def test_unchanged(tmp_path):
    d = diff(tmp_path, two_sections([P1], [P3]), two_sections([P1], [P3]))
    assert set(kinds(d)) == {K.UNCHANGED}
    assert not d.any_change


def test_modified_paragraph_reports_added_and_removed_words(tmp_path):
    d = diff(tmp_path, two_sections([P1], [P3]), two_sections([P1], [P3.replace("384", "396")]))
    (c,) = [c for c in d.paragraphs if c.kind is K.MODIFIED and not c.is_heading]
    assert c.added_text == "396" and c.removed_text == "384"
    assert c.similarity > 0.8


def test_inserted_paragraph(tmp_path):
    d = diff(
        tmp_path,
        two_sections([P1], [P3]),
        two_sections([P1], [P3, "A brand new limitation paragraph appears."]),
    )
    added = [c for c in d.paragraphs if c.kind is K.ADDED]
    assert len(added) == 1 and "limitation" in added[0].revised_text
    assert d.summary.paragraphs_added == 1


def test_deleted_paragraph(tmp_path):
    d = diff(tmp_path, two_sections([P1, P2], [P3]), two_sections([P1], [P3]))
    removed = [c for c in d.paragraphs if c.kind is K.REMOVED]
    assert len(removed) == 1 and removed[0].original_text == P2


def test_moved_paragraph_is_not_a_delete_plus_insert(tmp_path):
    d = diff(tmp_path, two_sections([P1, P2], [P3]), two_sections([P1], [P3, P2]))
    moved = [c for c in d.paragraphs if c.kind is K.MOVED]
    assert len(moved) == 1 and moved[0].original_text == P2 and moved[0].similarity == 1.0
    assert d.summary.paragraphs_added == 0 and d.summary.paragraphs_removed == 0
    assert moved[0].original_section_id != moved[0].revised_section_id


def test_reordered_paragraphs_in_one_section_are_moves(tmp_path):
    d = diff(tmp_path, two_sections([P1, P2], [P3]), two_sections([P2, P1], [P3]))
    assert d.summary.paragraphs_added == 0 and d.summary.paragraphs_removed == 0
    assert d.summary.paragraphs_moved >= 1


def test_renamed_heading(tmp_path):
    d = diff(
        tmp_path,
        two_sections([P1], [P3], "2 Research Method"),
        two_sections([P1], [P3], "2 Research Methodology"),
    )
    renamed = [m for m in d.sections if m.kind is SectionMatchKind.RENAMED]
    assert len(renamed) == 1
    assert renamed[0].original_heading.endswith("Method") and renamed[0].revised_heading.endswith(
        "Methodology"
    )
    heading_change = next(c for c in d.paragraphs if c.is_heading and c.kind is K.MODIFIED)
    assert heading_change.original_text != heading_change.revised_text


def test_renumbered_heading_matches_by_title(tmp_path):
    d = diff(tmp_path, two_sections([P1], [P3], "2 Method"), two_sections([P1], [P3], "3 Method"))
    assert not [
        m for m in d.sections if m.kind in (SectionMatchKind.ADDED, SectionMatchKind.REMOVED)
    ]


def test_added_section(tmp_path):
    b = two_sections([P1], [P3])
    b.heading("3 Limitations", 1).para("Recall bias may affect self-reported data in this study.")
    d = diff(tmp_path, two_sections([P1], [P3]), b)
    added = [m for m in d.sections if m.kind is SectionMatchKind.ADDED]
    assert [m.revised_heading for m in added] == ["3 Limitations"]
    assert added[0].words_after > 0 and d.summary.sections_added == 1


def test_removed_section(tmp_path):
    a = two_sections([P1], [P3])
    d = diff(tmp_path, a, DocxBuilder().heading("1 Introduction", 1).para(P1))
    assert d.summary.sections_removed == 1


# --- tables -------------------------------------------------------------------------


def table_doc(rows, caption="Table 4.2 Results by group"):
    b = DocxBuilder()
    b.heading("4 Results", 1)
    b.para(caption)
    b.table(rows)
    return b


BASE = [["Group", "Pct"], ["A", "40%"], ["B", "60%"]]


def test_table_changed_cell(tmp_path):
    d = diff(tmp_path, table_doc(BASE), table_doc([["Group", "Pct"], ["A", "45%"], ["B", "60%"]]))
    (t,) = d.tables
    assert t.kind is TableChangeKind.MODIFIED
    (cc,) = t.cell_changes
    assert (cc.row_label, cc.col_label, cc.original, cc.revised) == ("A", "Pct", "40%", "45%")


def test_table_row_added(tmp_path):
    d = diff(tmp_path, table_doc(BASE), table_doc([*BASE, ["C", "10%"]]))
    (t,) = d.tables
    assert t.kind is TableChangeKind.MODIFIED and t.rows_added == ["C"] and not t.rows_removed


def test_table_column_added(tmp_path):
    rows = [["Group", "Pct", "N"], ["A", "40%", "10"], ["B", "60%", "15"]]
    d = diff(tmp_path, table_doc(BASE), table_doc(rows))
    assert d.tables[0].columns_added == ["N"]


def test_table_removed(tmp_path):
    b = DocxBuilder().heading("4 Results", 1).para("Some text without a table.")
    d = diff(tmp_path, table_doc(BASE), b)
    assert [t.kind for t in d.tables] == [TableChangeKind.REMOVED]
    assert d.summary.tables_removed == 1


def test_table_added_and_caption_change(tmp_path):
    d = diff(tmp_path, table_doc(BASE), table_doc(BASE, "Table 4.2 Results by study group"))
    assert d.tables[0].caption_changed and d.tables[0].kind is TableChangeKind.MODIFIED
    b = DocxBuilder().heading("4 Results", 1).para("Text.")
    d2 = diff(tmp_path, b, table_doc(BASE))
    assert [t.kind for t in d2.tables] == [TableChangeKind.ADDED]


def test_unchanged_table(tmp_path):
    d = diff(tmp_path, table_doc(BASE), table_doc(BASE))
    assert d.tables[0].kind is TableChangeKind.UNCHANGED


# --- references ---------------------------------------------------------------------


def ref_doc(*refs):
    b = DocxBuilder().heading("1 Intro", 1).para("Text.")
    b.heading("References", 1)
    for r in refs:
        b.para(r)
    return b


def test_reference_changes(tmp_path):
    a = ref_doc(
        "Adams, R. (2016). Peer influence. Journal A.",
        "Brown, T. (2014). Health behaviour. Journal B.",
    )
    b = ref_doc(
        "Adams, R. (2016). Peer influence. Journal A.",
        "Brown, T. (2014). Health behaviour revised. Journal B.",
        "Lee, M. (2024). New paper. Journal C.",
    )
    r = diff(tmp_path, a, b).references
    assert r.added == ["Lee, M. (2024). New paper. Journal C."] and r.added_years == [2024]
    assert len(r.modified) == 1 and not r.removed
    assert (r.newest_original, r.newest_revised) == (2016, 2024)


def test_reference_removed(tmp_path):
    r = diff(
        tmp_path,
        ref_doc(
            "Adams, R. (2016). Peer influence. Journal A.", "Brown, T. (2014). Health. Journal B."
        ),
        ref_doc("Adams, R. (2016). Peer influence. Journal A."),
    ).references
    assert len(r.removed) == 1 and r.changed


def test_heading_with_only_subsections_is_matched_when_renamed(tmp_path):
    def m(heading):
        b = DocxBuilder().heading("1 Intro", 1).para(P1)
        b.heading(heading, 1).heading("2.1 Sample Size", 2).para(P3)
        return b

    d = diff(tmp_path, m("2 Methods"), m("2 Materials and Methods"))
    assert not [
        s for s in d.sections if s.kind in (SectionMatchKind.ADDED, SectionMatchKind.REMOVED)
    ]
    match = next(s for s in d.sections if s.original_heading == "2 Methods")
    assert match.revised_heading == "2 Materials and Methods"

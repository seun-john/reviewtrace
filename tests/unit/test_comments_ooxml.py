from __future__ import annotations

import hashlib
import zipfile

import pytest

from reviewtrace.core import extract_comments
from reviewtrace.extractors.docx import load_document
from reviewtrace.models.document import TrackedKind
from reviewtrace.utils.errors import DocumentReadError, UnsupportedFileTypeError
from tests.fixtures.docx_builder import Anchor, Del, DocxBuilder, Ins


def _doc(tmp_path, builder, name="d.docx"):
    return builder.save(tmp_path / name)


def sample() -> DocxBuilder:
    b = DocxBuilder()
    b.heading("3 Methods", 1)
    b.heading("3.4 Sampling Procedure", 2)
    b.para("Before paragraph.")
    b.para("A ", Anchor(1, "multistage sampling technique was adopted."), " Then more text.")
    b.para("After paragraph.")
    return b


def test_single_comment_with_anchor_and_context(tmp_path):
    b = sample().comment(
        1, "Explain this.", author="Prof. Smith", initials="PS", date="2024-03-01T09:00:00Z"
    )
    (c,) = extract_comments(_doc(tmp_path, b))
    assert c.text == "Explain this."
    assert c.author == "Prof. Smith" and c.initials == "PS"
    assert c.date == "2024-03-01T09:00:00Z"
    assert c.anchor_text == "multistage sampling technique was adopted."
    assert c.anchor.section == "3.4 Sampling Procedure"
    assert c.anchor.paragraph_start == 4
    assert c.anchor_paragraph_text.startswith("A multistage sampling")
    assert c.context_before[-1] == "Before paragraph."
    assert c.context_after[0] == "After paragraph."


def test_multiple_comments_keep_their_own_anchors(tmp_path):
    b = DocxBuilder()
    b.heading("1 Intro", 1)
    b.para(Anchor(1, "first anchor"), " and ", Anchor(2, "second anchor"))
    b.para(Anchor(3, "third"))
    b.comment(1, "one").comment(2, "two").comment(3, "three")
    comments = extract_comments(_doc(tmp_path, b))
    assert [c.anchor_text for c in comments] == ["first anchor", "second anchor", "third"]
    assert [c.anchor.paragraph_start for c in comments] == [2, 2, 3]


def test_comment_without_useful_anchor(tmp_path):
    b = DocxBuilder()
    b.heading("1 Intro", 1)
    b.para("Some text.")
    b.comment(7, "General remark with no range.")
    (c,) = extract_comments(_doc(tmp_path, b))
    assert c.anchor_text == ""
    assert c.anchor.paragraph_start is None
    assert c.text == "General remark with no range."


def test_comment_with_multiple_paragraphs(tmp_path):
    b = sample().comment(1, ["First point.", "Second point."])
    (c,) = extract_comments(_doc(tmp_path, b))
    assert c.paragraphs == ["First point.", "Second point."]
    assert c.text == "First point.\nSecond point."


def test_anchor_spanning_paragraphs(tmp_path):
    b = DocxBuilder()
    b.heading("1 Intro", 1)
    b.raw(
        '<w:p><w:r><w:t>Start </w:t></w:r><w:commentRangeStart w:id="1"/>'
        "<w:r><w:t>first half</w:t></w:r></w:p>"
        '<w:p><w:r><w:t>second half</w:t></w:r><w:commentRangeEnd w:id="1"/>'
        '<w:r><w:commentReference w:id="1"/></w:r></w:p>'
    )
    b.comment(1, "Merge these.")
    (c,) = extract_comments(_doc(tmp_path, b))
    assert c.anchor_text == "first half\nsecond half"
    assert (c.anchor.paragraph_start, c.anchor.paragraph_end) == (2, 3)


def test_thread_replies_and_word_resolved_flag(tmp_path):
    b = (
        sample()
        .comment(1, "Explain this.", done=True)
        .comment(2, "Done, see 3.4.", author="Student", parent=1)
    )
    comments = extract_comments(_doc(tmp_path, b))
    root = next(c for c in comments if c.id == "1")
    reply = next(c for c in comments if c.id == "2")
    assert root.word_comment_resolved is True
    assert [r.text for r in root.replies] == ["Done, see 3.4."]
    assert reply.parent_id == "1"


def test_resolved_flag_absent_without_extended_part(tmp_path):
    (c,) = extract_comments(_doc(tmp_path, sample().comment(1, "x")))
    assert c.word_comment_resolved is None


def test_comment_inside_table_cell_and_footnote(tmp_path):
    b = DocxBuilder()
    b.heading("4 Results", 1)
    b.table([["Group", "Pct"], [[Anchor(1, "A")], "45%"]])
    b.para("x")
    b.footnote(1, "Note ", Anchor(2, "text"))
    b.comment(1, "Check this cell.").comment(2, "Footnote issue.")
    c1, c2 = extract_comments(_doc(tmp_path, b))
    assert c1.anchor.table_index == 0 and c1.anchor.cell == (1, 0)
    assert c2.anchor.note == "in footnote 1" and c2.anchor_text == "text"


def test_tracked_insertions_and_deletions_are_read(tmp_path):
    b = DocxBuilder()
    b.heading("2 Methods", 1)
    b.para(
        "Alpha was measured.",
        Ins(" Cronbach's alpha was 0.86.", author="Ann", date="2024-05-01T10:00:00Z"),
        Del(" Old claim."),
    )
    doc = load_document(_doc(tmp_path, b))
    ins = next(t for t in doc.tracked_changes if t.kind is TrackedKind.INSERTION)
    dele = next(t for t in doc.tracked_changes if t.kind is TrackedKind.DELETION)
    assert ins.text == "Cronbach's alpha was 0.86."
    assert ins.author == "Ann" and ins.date.startswith("2024-05-01")
    assert dele.text == "Old claim."
    assert ins.section_id == "S1" and ins.paragraph == 2
    # The final view contains insertions and excludes deletions.
    assert "0.86" in doc.paragraph(2).text and "Old claim" not in doc.paragraph(2).text


def test_huge_comment_is_truncated_not_fatal(tmp_path):
    huge = "word " * 100_000
    (c,) = extract_comments(_doc(tmp_path, sample().comment(1, huge)))
    assert c.truncated and len(c.text) <= 20_001


def test_missing_file(tmp_path):
    with pytest.raises(DocumentReadError, match="not found"):
        load_document(tmp_path / "nope.docx")


def test_unsupported_type(tmp_path):
    p = tmp_path / "old.doc"
    p.write_bytes(b"x")
    with pytest.raises(UnsupportedFileTypeError, match="Convert legacy"):
        load_document(p)


def test_invalid_docx(tmp_path):
    p = tmp_path / "bad.docx"
    p.write_bytes(b"this is not a zip file")
    with pytest.raises(DocumentReadError):
        load_document(p)


def test_zip_without_word_parts(tmp_path):
    p = tmp_path / "empty.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(DocumentReadError):
        load_document(p)


def test_malformed_comments_xml(tmp_path):
    p = (
        sample()
        .comment(1, "x")
        .save(tmp_path / "m.docx", overrides={"word/comments.xml": "<w:comments><oops"})
    )
    with pytest.raises(DocumentReadError):
        extract_comments(p)


def test_malformed_document_xml(tmp_path):
    p = sample().save(tmp_path / "m.docx", overrides={"word/document.xml": "<w:document><broken"})
    with pytest.raises(DocumentReadError):
        load_document(p)


def test_xml_entities_are_not_expanded(tmp_path):
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:comment w:id="1" w:author="x"><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:comment>'
        "</w:comments>"
    )
    p = sample().comment(1, "x").save(tmp_path / "b.docx", overrides={"word/comments.xml": bomb})
    comments = extract_comments(p)
    assert len(comments[0].text) < 100  # entity left unresolved rather than expanded


def test_source_file_is_not_modified(tmp_path):
    p = _doc(tmp_path, sample().comment(1, "x"))
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    extract_comments(p)
    load_document(p)
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before

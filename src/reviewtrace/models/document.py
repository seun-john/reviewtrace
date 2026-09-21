"""Normalised document model.

Locators are logical (section, paragraph number, table/cell), never page numbers:
page numbers depend on rendering and are not stored in DOCX.

Paragraph numbers are 1-based and count non-empty *body* paragraphs in document order.
Paragraphs inside tables are addressed by ``(table_index, row, col)`` instead.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field, PrivateAttr

from reviewtrace.utils.text import word_count


class Location(BaseModel):
    """A logical position inside a document."""

    section_id: str | None = None
    section: str | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    table_index: int | None = None
    cell: tuple[int, int] | None = None
    note: str | None = None

    def label(self) -> str:
        parts: list[str] = []
        if self.section:
            parts.append(
                f"Section {self.section}" if _starts_numbered(self.section) else self.section
            )
        if self.table_index is not None:
            cell = f", cell R{self.cell[0] + 1}C{self.cell[1] + 1}" if self.cell else ""
            parts.append(f"Table #{self.table_index + 1}{cell}")
        if self.paragraph_start is not None:
            if self.paragraph_end is not None and self.paragraph_end != self.paragraph_start:
                parts.append(f"paragraphs {self.paragraph_start}–{self.paragraph_end}")
            else:
                parts.append(f"paragraph {self.paragraph_start}")
        if self.note:
            parts.append(self.note)
        return ", ".join(parts) if parts else "unlocated"


def _starts_numbered(text: str) -> bool:
    return bool(re.match(r"^\d", text.strip()))


class Paragraph(BaseModel):
    number: int
    text: str
    style: str = ""
    is_heading: bool = False
    heading_level: int = 0
    is_list_item: bool = False
    has_image: bool = False
    section_id: str = "S0"


class Table(BaseModel):
    index: int
    rows: list[list[str]]
    caption: str | None = None
    label: str | None = None
    section_id: str = "S0"
    after_paragraph: int = 0

    @property
    def header(self) -> list[str]:
        return self.rows[0] if self.rows else []


class Section(BaseModel):
    id: str
    heading: str
    number: str | None = None
    title: str = ""
    level: int = 0
    parent_id: str | None = None
    heading_paragraph: int | None = None
    paragraph_numbers: list[int] = Field(default_factory=list)
    table_indices: list[int] = Field(default_factory=list)


class Reference(BaseModel):
    index: int
    raw: str
    year: int | None = None
    paragraph: int | None = None


class TrackedKind(str, Enum):
    INSERTION = "insertion"
    DELETION = "deletion"


class TrackedChange(BaseModel):
    """A tracked revision found in the file (read-only; never accepted or rejected)."""

    id: str
    kind: TrackedKind
    author: str = ""
    date: str | None = None
    text: str
    moved: bool = False
    paragraph: int | None = None
    section_id: str | None = None
    table_index: int | None = None
    cell: tuple[int, int] | None = None
    story: str = "body"


class CommentReply(BaseModel):
    id: str
    author: str = ""
    date: str | None = None
    text: str = ""


class ReviewComment(BaseModel):
    """A Word comment together with the text it is attached to."""

    id: str
    author: str = ""
    initials: str = ""
    date: str | None = None
    text: str
    paragraphs: list[str] = Field(default_factory=list)
    anchor_text: str = ""
    anchor: Location = Field(default_factory=Location)
    context_before: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)
    anchor_paragraph_text: str = ""
    parent_id: str | None = None
    replies: list[CommentReply] = Field(default_factory=list)
    word_comment_resolved: bool | None = None
    truncated: bool = False


class Footnote(BaseModel):
    id: str
    kind: str = "footnote"
    text: str


class Document(BaseModel):
    """Everything ReviewTrace knows about one DOCX file."""

    path: str = ""
    title: str = ""
    paragraphs: list[Paragraph] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    tracked_changes: list[TrackedChange] = Field(default_factory=list)
    comments: list[ReviewComment] = Field(default_factory=list)
    footnotes: list[Footnote] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    _by_number: dict[int, Paragraph] = PrivateAttr(default_factory=dict)
    _sec_by_id: dict[str, Section] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: object) -> None:
        self.reindex()

    def reindex(self) -> None:
        self._by_number = {p.number: p for p in self.paragraphs}
        self._sec_by_id = {s.id: s for s in self.sections}

    # -- lookups -----------------------------------------------------------------
    def paragraph(self, number: int) -> Paragraph | None:
        return self._by_number.get(number)

    def section(self, section_id: str | None) -> Section | None:
        return self._sec_by_id.get(section_id) if section_id else None

    def text_of(self, number: int) -> str:
        p = self._by_number.get(number)
        return p.text if p else ""

    def is_heading(self, number: int) -> bool:
        p = self._by_number.get(number)
        return bool(p and p.is_heading)

    def section_of(self, paragraph_number: int) -> Section | None:
        p = self.paragraph(paragraph_number)
        return self.section(p.section_id) if p else None

    def children_of(self, section_id: str) -> list[Section]:
        return [s for s in self.sections if s.parent_id == section_id]

    def subtree_ids(self, section_id: str) -> list[str]:
        out = [section_id]
        for child in self.children_of(section_id):
            out.extend(self.subtree_ids(child.id))
        return out

    # -- text --------------------------------------------------------------------
    def section_paragraphs(self, section_id: str, subtree: bool = False) -> list[Paragraph]:
        ids = set(self.subtree_ids(section_id)) if subtree else {section_id}
        return [p for p in self.paragraphs if p.section_id in ids]

    def body_paragraphs(self, section_id: str, subtree: bool = False) -> list[Paragraph]:
        return [p for p in self.section_paragraphs(section_id, subtree) if not p.is_heading]

    def section_text(self, section_id: str, subtree: bool = False) -> str:
        return "\n".join(p.text for p in self.body_paragraphs(section_id, subtree))

    def section_words(self, section_id: str, subtree: bool = False) -> int:
        return sum(word_count(p.text) for p in self.body_paragraphs(section_id, subtree))

    def full_text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs)

    def find_section(self, key: str) -> Section | None:
        """Find a section by number (``2.3``) or by heading text (case-insensitive)."""
        from reviewtrace.utils.text import char_similarity, normalize

        k = key.strip()
        for s in self.sections:
            if s.number and s.number == k:
                return s
        nk = normalize(k)
        for s in self.sections:
            if normalize(s.heading) == nk or normalize(s.title) == nk:
                return s
        best: tuple[float, Section | None] = (0.0, None)
        for s in self.sections:
            if not s.title:
                continue
            score = char_similarity(s.title, k)
            if score > best[0]:
                best = (score, s)
        return best[1] if best[0] >= 0.85 else None

    def location_of(self, paragraph_start: int, paragraph_end: int | None = None) -> Location:
        sec = self.section_of(paragraph_start)
        return Location(
            section_id=sec.id if sec else None,
            section=sec.heading if sec and sec.heading else None,
            paragraph_start=paragraph_start,
            paragraph_end=paragraph_end,
        )

    def paragraph_range(self, numbers: list[int]) -> Location:
        if not numbers:
            return Location()
        return self.location_of(min(numbers), max(numbers))

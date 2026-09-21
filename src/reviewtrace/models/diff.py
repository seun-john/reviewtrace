"""Results of comparing an original and a revised document."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, PrivateAttr

from reviewtrace.models.document import Location


class ChangeKind(str, Enum):
    UNCHANGED = "unchanged"
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    MOVED = "moved"


class ParagraphChange(BaseModel):
    kind: ChangeKind
    original: int | None = None
    revised: int | None = None
    similarity: float = 0.0
    original_text: str = ""
    revised_text: str = ""
    added_text: str = ""
    removed_text: str = ""
    original_section_id: str | None = None
    revised_section_id: str | None = None
    is_heading: bool = False
    is_caption: bool = False
    text_changed: bool = False

    @property
    def changed(self) -> bool:
        return self.kind is not ChangeKind.UNCHANGED


class SectionMatchKind(str, Enum):
    EXACT = "exact"
    RENAMED = "renamed"
    CONTENT = "content"
    ADDED = "added"
    REMOVED = "removed"


class SectionMatch(BaseModel):
    kind: SectionMatchKind
    original_id: str | None = None
    revised_id: str | None = None
    original_heading: str = ""
    revised_heading: str = ""
    heading_similarity: float = 0.0
    content_similarity: float = 0.0
    words_before: int = 0
    words_after: int = 0
    paragraphs_added: int = 0
    paragraphs_removed: int = 0
    paragraphs_modified: int = 0
    paragraphs_moved_in: int = 0
    paragraphs_moved_out: int = 0

    @property
    def changed(self) -> bool:
        return (
            self.kind
            in (SectionMatchKind.ADDED, SectionMatchKind.REMOVED, SectionMatchKind.RENAMED)
            or self.words_before != self.words_after
            or any(
                (
                    self.paragraphs_added,
                    self.paragraphs_removed,
                    self.paragraphs_modified,
                    self.paragraphs_moved_in,
                    self.paragraphs_moved_out,
                )
            )
        )


class CellChange(BaseModel):
    row: int
    col: int
    row_label: str = ""
    col_label: str = ""
    original: str = ""
    revised: str = ""


class TableChangeKind(str, Enum):
    UNCHANGED = "unchanged"
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


class TableChange(BaseModel):
    kind: TableChangeKind
    original_index: int | None = None
    revised_index: int | None = None
    original_caption: str | None = None
    revised_caption: str | None = None
    caption_changed: bool = False
    rows_added: list[str] = Field(default_factory=list)
    rows_removed: list[str] = Field(default_factory=list)
    columns_added: list[str] = Field(default_factory=list)
    columns_removed: list[str] = Field(default_factory=list)
    cell_changes: list[CellChange] = Field(default_factory=list)

    def describe(self) -> str:
        bits: list[str] = []
        if self.rows_added:
            bits.append(f"{len(self.rows_added)} row(s) added")
        if self.rows_removed:
            bits.append(f"{len(self.rows_removed)} row(s) removed")
        if self.columns_added:
            bits.append(f"{len(self.columns_added)} column(s) added")
        if self.columns_removed:
            bits.append(f"{len(self.columns_removed)} column(s) removed")
        if self.cell_changes:
            bits.append(f"{len(self.cell_changes)} cell(s) changed")
        if self.caption_changed:
            bits.append("caption changed")
        return ", ".join(bits) or self.kind.value


class ReferenceChange(BaseModel):
    original_count: int = 0
    revised_count: int = 0
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    modified: list[tuple[str, str]] = Field(default_factory=list)
    added_years: list[int | None] = Field(default_factory=list)
    original_years: list[int] = Field(default_factory=list)
    revised_years: list[int] = Field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.modified)

    @property
    def newest_original(self) -> int | None:
        return max(self.original_years) if self.original_years else None

    @property
    def newest_revised(self) -> int | None:
        return max(self.revised_years) if self.revised_years else None


class DiffSummary(BaseModel):
    paragraphs_unchanged: int = 0
    paragraphs_added: int = 0
    paragraphs_removed: int = 0
    paragraphs_modified: int = 0
    paragraphs_moved: int = 0
    sections_added: int = 0
    sections_removed: int = 0
    sections_renamed: int = 0
    tables_added: int = 0
    tables_removed: int = 0
    tables_modified: int = 0
    references_added: int = 0
    references_removed: int = 0
    tracked_insertions: int = 0
    tracked_deletions: int = 0


class DocumentDiff(BaseModel):
    sections: list[SectionMatch] = Field(default_factory=list)
    paragraphs: list[ParagraphChange] = Field(default_factory=list)
    tables: list[TableChange] = Field(default_factory=list)
    references: ReferenceChange = Field(default_factory=ReferenceChange)
    summary: DiffSummary = Field(default_factory=DiffSummary)

    _by_orig: dict[int, ParagraphChange] = PrivateAttr(default_factory=dict)
    _sec_by_orig: dict[str, SectionMatch] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: object) -> None:
        self.reindex()

    def reindex(self) -> None:
        self._by_orig = {c.original: c for c in self.paragraphs if c.original is not None}
        self._sec_by_orig = {m.original_id: m for m in self.sections if m.original_id}

    def change_for_original(self, number: int) -> ParagraphChange | None:
        return self._by_orig.get(number)

    def section_for_original(self, section_id: str) -> SectionMatch | None:
        return self._sec_by_orig.get(section_id)

    @property
    def any_change(self) -> bool:
        return any(c.changed for c in self.paragraphs) or any(
            t.kind is not TableChangeKind.UNCHANGED for t in self.tables
        )


__all__ = [
    "CellChange",
    "ChangeKind",
    "DiffSummary",
    "DocumentDiff",
    "Location",
    "ParagraphChange",
    "ReferenceChange",
    "SectionMatch",
    "SectionMatchKind",
    "TableChange",
    "TableChangeKind",
]

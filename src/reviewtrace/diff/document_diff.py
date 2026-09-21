"""Hierarchical comparison: sections -> paragraphs -> words, plus tables and references."""

from __future__ import annotations

from reviewtrace.diff.paragraph_matcher import match_paragraphs
from reviewtrace.diff.reference_diff import diff_references
from reviewtrace.diff.section_matcher import match_sections
from reviewtrace.diff.table_diff import diff_tables
from reviewtrace.models.diff import (
    ChangeKind,
    DiffSummary,
    DocumentDiff,
    ParagraphChange,
    SectionMatch,
    SectionMatchKind,
    TableChangeKind,
)
from reviewtrace.models.document import Document, TrackedKind


def _fill_section_stats(matches: list[SectionMatch], changes: list[ParagraphChange]) -> None:
    by_o = {m.original_id: m for m in matches if m.original_id}
    by_r = {m.revised_id: m for m in matches if m.revised_id}
    for c in changes:
        if c.is_heading:
            continue
        if c.kind is ChangeKind.ADDED and c.revised_section_id in by_r:
            by_r[c.revised_section_id].paragraphs_added += 1
        elif c.kind is ChangeKind.REMOVED and c.original_section_id in by_o:
            by_o[c.original_section_id].paragraphs_removed += 1
        elif c.kind is ChangeKind.MODIFIED and c.revised_section_id in by_r:
            by_r[c.revised_section_id].paragraphs_modified += 1
        elif c.kind is ChangeKind.MOVED:
            if c.revised_section_id in by_r:
                by_r[c.revised_section_id].paragraphs_moved_in += 1
            if c.original_section_id in by_o:
                by_o[c.original_section_id].paragraphs_moved_out += 1


def diff_documents(original: Document, revised: Document) -> DocumentDiff:
    sections = match_sections(original, revised)
    paragraphs = match_paragraphs(original, revised, sections)
    _fill_section_stats(sections, paragraphs)
    tables = diff_tables(original, revised)
    references = diff_references(original, revised)

    summary = DiffSummary(
        paragraphs_unchanged=sum(c.kind is ChangeKind.UNCHANGED for c in paragraphs),
        paragraphs_added=sum(c.kind is ChangeKind.ADDED for c in paragraphs),
        paragraphs_removed=sum(c.kind is ChangeKind.REMOVED for c in paragraphs),
        paragraphs_modified=sum(c.kind is ChangeKind.MODIFIED for c in paragraphs),
        paragraphs_moved=sum(c.kind is ChangeKind.MOVED for c in paragraphs),
        sections_added=sum(m.kind is SectionMatchKind.ADDED for m in sections),
        sections_removed=sum(m.kind is SectionMatchKind.REMOVED for m in sections),
        sections_renamed=sum(m.kind is SectionMatchKind.RENAMED for m in sections),
        tables_added=sum(t.kind is TableChangeKind.ADDED for t in tables),
        tables_removed=sum(t.kind is TableChangeKind.REMOVED for t in tables),
        tables_modified=sum(t.kind is TableChangeKind.MODIFIED for t in tables),
        references_added=len(references.added),
        references_removed=len(references.removed),
        tracked_insertions=sum(t.kind is TrackedKind.INSERTION for t in revised.tracked_changes),
        tracked_deletions=sum(t.kind is TrackedKind.DELETION for t in revised.tracked_changes),
    )
    return DocumentDiff(
        sections=sections,
        paragraphs=paragraphs,
        tables=tables,
        references=references,
        summary=summary,
    )

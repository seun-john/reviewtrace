"""Locate what a request refers to, and gather objective observations about it.

``AuditContext`` wraps the two documents and their diff. ``locate_target`` decides which
paragraphs/section/table a request is about (and how sure that is). The helpers here turn
diff results into :class:`~reviewtrace.models.evidence.Evidence` objects; they never decide
a status.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from reviewtrace.analysis.confidence import TargetQuality
from reviewtrace.analysis.request_spec import RequestSpec
from reviewtrace.diff.text_diff import added_sentences
from reviewtrace.extractors.docx import caption_info
from reviewtrace.models.diff import (
    ChangeKind,
    DocumentDiff,
    ParagraphChange,
)
from reviewtrace.models.document import Document, Location, Section, Table
from reviewtrace.models.evidence import Evidence, EvidenceDirection, EvidenceType
from reviewtrace.models.issue import ReviewIssue
from reviewtrace.utils.text import (
    char_similarity,
    contains_number,
    content_terms,
    normalize,
    sentences,
    similarity,
    stems_in,
    truncate,
    word_count,
)

EXCERPT = 300


@dataclass
class AddedUnit:
    """Text that is new to the document (not merely moved)."""

    text: str
    paragraph: int
    section_id: str | None
    origin: str  # "paragraph" | "sentence"


@dataclass
class Target:
    quality: TargetQuality = TargetQuality.NONE
    original_paragraphs: list[int] = field(default_factory=list)
    original_section: Section | None = None
    revised_section: Section | None = None
    section_removed: bool = False
    table: Table | None = None
    figure_paragraph: int | None = None
    anchor_text: str = ""
    subtree: bool = False
    how: str = "no location could be determined"

    @property
    def located(self) -> bool:
        return self.quality > TargetQuality.NONE


@dataclass
class Scope:
    original_ids: set[str]
    revised_ids: set[str]
    changes: list[ParagraphChange]
    words_before: int
    words_after: int
    whole_document: bool

    @property
    def changed(self) -> bool:
        return any(c.changed for c in self.changes)


class AuditContext:
    def __init__(self, original: Document, revised: Document, diff: DocumentDiff) -> None:
        self.original = original
        self.revised = revised
        self.diff = diff
        self._o_norm = {p.number: normalize(p.text) for p in original.paragraphs}
        self._r_norm = {p.number: normalize(p.text) for p in revised.paragraphs}

    # -- text search ----------------------------------------------------------------
    def norm_map(self, doc: Document) -> dict[int, str]:
        return self._o_norm if doc is self.original else self._r_norm

    def find_in(self, doc: Document, needle: str, section_ids: set[str] | None = None) -> list[int]:
        n = normalize(needle)
        if not n:
            return []
        norms = self.norm_map(doc)
        out: list[int] = []
        for p in doc.paragraphs:
            if section_ids is not None and p.section_id not in section_ids:
                continue
            if n in norms[p.number]:
                out.append(p.number)
        return out

    def count_text(self, doc: Document, needle: str, section_ids: set[str] | None = None) -> int:
        n = normalize(needle)
        if not n:
            return 0
        pattern = re.compile(r"(?<![\w])" + re.escape(n) + r"(?![\w])")
        total = 0
        for p in doc.paragraphs:
            if section_ids is not None and p.section_id not in section_ids:
                continue
            total += len(pattern.findall(self.norm_map(doc)[p.number]))
        return total

    def find_number(
        self, doc: Document, number: str, section_ids: set[str] | None = None
    ) -> list[int]:
        return [
            p.number
            for p in doc.paragraphs
            if (section_ids is None or p.section_id in section_ids)
            and contains_number(p.text, number)
        ]

    # -- locations ------------------------------------------------------------------
    def rev_loc(self, numbers: list[int] | int | None) -> Location | None:
        if numbers is None:
            return None
        nums = [numbers] if isinstance(numbers, int) else numbers
        return self.revised.paragraph_range(sorted(nums)) if nums else None

    def orig_loc(self, numbers: list[int] | int | None) -> Location | None:
        if numbers is None:
            return None
        nums = [numbers] if isinstance(numbers, int) else numbers
        return self.original.paragraph_range(sorted(nums)) if nums else None

    def section_loc(self, doc: Document, section: Section | None) -> Location | None:
        if section is None:
            return None
        nums = section.paragraph_numbers
        body = [n for n in nums if not doc.is_heading(n)]
        loc = doc.paragraph_range(body or nums)
        loc.section_id, loc.section = section.id, section.heading or None
        return loc

    # -- targeting ------------------------------------------------------------------
    def _section_target(self, section: Section, quality: TargetQuality, how: str) -> Target:
        match = self.diff.section_for_original(section.id)
        rev_sec = None
        removed = False
        if match is None or match.revised_id is None:
            removed = True
        else:
            rev_sec = self.revised.section(match.revised_id)
        return Target(
            quality=quality,
            original_section=section,
            revised_section=rev_sec,
            section_removed=removed,
            subtree=True,
            how=how,
        )

    def _paragraph_target(
        self, numbers: list[int], quality: TargetQuality, anchor: str, how: str
    ) -> Target:
        first = self.original.paragraph(numbers[0])
        section = self.original.section(first.section_id) if first else None
        tgt = Target(
            quality=quality,
            original_paragraphs=numbers,
            original_section=section,
            anchor_text=anchor,
            how=how,
        )
        if section is not None:
            match = self.diff.section_for_original(section.id)
            if match and match.revised_id:
                tgt.revised_section = self.revised.section(match.revised_id)
            else:
                tgt.section_removed = True
        return tgt

    def find_anchor(self, anchor: str, prefer_section: Section | None) -> tuple[list[int], bool]:
        """Paragraph numbers for an anchor: exact substring first, then best fuzzy match."""
        hits = self.find_in(self.original, anchor)
        if hits:
            if len(hits) > 1 and prefer_section is not None:
                narrowed = [
                    h
                    for h in hits
                    if (sec := self.original.section_of(h)) and sec.id == prefer_section.id
                ]
                hits = narrowed or hits
            return hits[:1], len(hits) == 1 or prefer_section is not None
        best: tuple[float, int] = (0.0, 0)
        for p in self.original.paragraphs:
            score = similarity(anchor, p.text)
            for s in sentences(p.text):
                score = max(score, similarity(anchor, s))
            if score > best[0]:
                best = (score, p.number)
        if best[0] >= 0.7:
            return [best[1]], False
        return [], False


_HEADING_WORDS = re.compile(r"[a-z]+")


def locate_target(issue: ReviewIssue, spec: RequestSpec, ctx: AuditContext) -> Target:
    orig = ctx.original
    loc = issue.anchor_location

    # A. table the comment was written in
    if loc.table_index is not None and loc.table_index < len(orig.tables):
        table = orig.tables[loc.table_index]
        sec = orig.section(table.section_id)
        tgt = Target(
            quality=TargetQuality.EXACT,
            table=table,
            original_section=sec,
            how=f"table #{table.index + 1}",
        )
        if sec is not None:
            m = ctx.diff.section_for_original(sec.id)
            tgt.revised_section = ctx.revised.section(m.revised_id) if m and m.revised_id else None
        return tgt

    # B. explicit paragraph numbers (Word anchors, issues-file locations)
    if loc.paragraph_start:
        end = loc.paragraph_end or loc.paragraph_start
        nums = [n for n in range(loc.paragraph_start, end + 1) if orig.paragraph(n)]
        if nums:
            joined = normalize(" ".join(orig.text_of(n) for n in nums))
            anchor = issue.anchor_text
            if not anchor or normalize(anchor) in joined:
                where = (
                    f"paragraph {nums[0]}" if len(nums) == 1 else f"paragraphs {nums[0]}–{nums[-1]}"
                )
                return ctx._paragraph_target(
                    nums, TargetQuality.EXACT, anchor, f"anchored at {where}"
                )

    # C. anchor text search
    prefer = orig.find_section(loc.section) if loc.section else None
    if issue.anchor_text.strip():
        nums, exact = ctx.find_anchor(issue.anchor_text, prefer)
        if nums:
            quality = TargetQuality.EXACT if exact else TargetQuality.FUZZY
            how = "anchor text found" + ("" if exact else " approximately")
            return ctx._paragraph_target(nums, quality, issue.anchor_text, how)

    # D. table / figure named in the comment
    for label in spec.table_refs:
        for t in orig.tables:
            if t.label and normalize(t.label) == normalize(label):
                sec = orig.section(t.section_id)
                tgt = Target(
                    quality=TargetQuality.EXACT,
                    table=t,
                    original_section=sec,
                    how=f"{label} named in comment",
                )
                if sec is not None:
                    m = ctx.diff.section_for_original(sec.id)
                    tgt.revised_section = (
                        ctx.revised.section(m.revised_id) if m and m.revised_id else None
                    )
                return tgt
    for label in spec.figure_refs:
        for p in orig.paragraphs:
            info = caption_info(p.text)
            if info and info[0] == "figure" and normalize(f"Figure {info[1]}") == normalize(label):
                tgt = ctx._paragraph_target(
                    [p.number], TargetQuality.EXACT, p.text, f"{label} named in comment"
                )
                tgt.figure_paragraph = p.number
                return tgt

    # E. section named in the location or in the comment text
    section_keys = ([loc.section] if loc.section else []) + spec.section_refs
    for key in section_keys:
        sec = orig.find_section(key)
        if sec is not None and sec.id != "S0":
            return ctx._section_target(
                sec, TargetQuality.SECTION, f"section {sec.number or sec.heading} named"
            )

    # F. a quoted phrase that occurs in the original
    for phrase in spec.phrases:
        if len(phrase.split()) >= 2:
            hits = ctx.find_in(orig, phrase)
            if len(hits) == 1:
                return ctx._paragraph_target(
                    hits[:1], TargetQuality.FUZZY, phrase, "quoted phrase found in original"
                )

    # G. a section whose whole (short) heading is covered by the request's own terms
    terms = set(spec.terms)
    candidates: list[Section] = []
    for sec in orig.sections[1:]:
        stems = content_terms(sec.title)
        if stems and len(stems) <= 3 and all(s in terms for s in stems):
            candidates.append(sec)
    if len(candidates) == 1:
        return ctx._section_target(
            candidates[0],
            TargetQuality.SECTION,
            f"section '{candidates[0].title}' inferred from the request wording",
        )

    # H. topic words: three or more request terms that co-occur in exactly one original paragraph
    orig_stems = (
        set().union(*(stems_in(p.text) for p in orig.paragraphs)) if orig.paragraphs else set()
    )
    topic = [t for t in spec.terms if t in orig_stems]
    if len(topic) >= 3:
        holders = [
            p for p in orig.paragraphs if not p.is_heading and set(topic) <= stems_in(p.text)
        ]
        if len(holders) == 1:
            sec = orig.section(holders[0].section_id)
            if sec is not None and sec.id != "S0":
                return ctx._section_target(
                    sec,
                    TargetQuality.SECTION,
                    "section inferred: its text is the only place that contains the request's topic words",
                )
    return Target()


# ---------------------------------------------------------------------------------
# Scope and added text
# ---------------------------------------------------------------------------------


def build_scope(ctx: AuditContext, target: Target) -> Scope:
    diff = ctx.diff
    if target.original_section is None or target.section_removed:
        if target.original_section is not None and target.section_removed:
            ids = {target.original_section.id}
            changes = [
                c for c in diff.paragraphs if c.original_section_id in ids and not c.is_heading
            ]
            return Scope(
                ids,
                set(),
                changes,
                ctx.original.section_words(target.original_section.id),
                0,
                False,
            )
        changes = [c for c in diff.paragraphs if not c.is_heading]
        return Scope(
            {s.id for s in ctx.original.sections},
            {s.id for s in ctx.revised.sections},
            changes,
            sum(ctx.original.section_words(s.id) for s in ctx.original.sections),
            sum(ctx.revised.section_words(s.id) for s in ctx.revised.sections),
            True,
        )
    o_ids = (
        set(ctx.original.subtree_ids(target.original_section.id))
        if target.subtree
        else {target.original_section.id}
    )
    r_ids: set[str] = set()
    if target.revised_section is not None:
        r_ids = (
            set(ctx.revised.subtree_ids(target.revised_section.id))
            if target.subtree
            else {target.revised_section.id}
        )
    changes = [
        c
        for c in diff.paragraphs
        if not c.is_heading
        and (
            (c.revised_section_id in r_ids)
            or (c.revised is None and c.original_section_id in o_ids)
        )
    ]
    words_before = sum(ctx.original.section_words(i) for i in o_ids)
    words_after = sum(ctx.revised.section_words(i) for i in r_ids)
    return Scope(o_ids, r_ids, changes, words_before, words_after, False)


def added_units(scope: Scope) -> list[AddedUnit]:
    units: list[AddedUnit] = []
    for c in scope.changes:
        if c.revised is None:
            continue
        if c.kind is ChangeKind.ADDED:
            units.append(AddedUnit(c.revised_text, c.revised, c.revised_section_id, "paragraph"))
        elif c.kind in (ChangeKind.MODIFIED, ChangeKind.MOVED) and c.text_changed:
            fresh = added_sentences(c.original_text, c.revised_text)
            if fresh:
                units.append(
                    AddedUnit(" ".join(fresh), c.revised, c.revised_section_id, "sentence")
                )
            elif c.added_text and word_count(c.added_text) >= 3:
                units.append(AddedUnit(c.added_text, c.revised, c.revised_section_id, "sentence"))
    return units


def changes_for_paragraphs(ctx: AuditContext, numbers: list[int]) -> list[ParagraphChange]:
    out = []
    for n in numbers:
        c = ctx.diff.change_for_original(n)
        if c is not None:
            out.append(c)
    return out


# ---------------------------------------------------------------------------------
# Evidence builders
# ---------------------------------------------------------------------------------

S, X, C = EvidenceDirection.SUPPORTS, EvidenceDirection.CONTRADICTS, EvidenceDirection.CONTEXT


def ev_added(
    ctx: AuditContext, units: list[AddedUnit], direction: EvidenceDirection, note: str = ""
) -> list[Evidence]:
    if not units:
        return []
    numbers = sorted({u.paragraph for u in units})
    words = sum(word_count(u.text) for u in units)
    loc = ctx.rev_loc(numbers)
    where = loc.label() if loc else "the revised document"
    joined = " ".join(u.text for u in units)
    explanation = f"{words} words of new text at {where}" + (f"; {note}" if note else "")
    return [
        Evidence(
            type=EvidenceType.TEXT_ADDED,
            direction=direction,
            revised_location=loc,
            revised_text=truncate(joined, EXCERPT),
            explanation=explanation,
        )
    ]


def ev_change(
    ctx: AuditContext, c: ParagraphChange, direction: EvidenceDirection, note: str = ""
) -> Evidence:
    o_loc = ctx.orig_loc(c.original) if c.original else None
    r_loc = ctx.rev_loc(c.revised) if c.revised else None
    if c.kind is ChangeKind.REMOVED:
        etype = EvidenceType.TEXT_REMOVED
        expl = f"paragraph {c.original} was removed"
    elif c.kind is ChangeKind.ADDED:
        etype = EvidenceType.TEXT_ADDED
        expl = f"paragraph {c.revised} was added"
    elif c.kind is ChangeKind.MOVED:
        etype = EvidenceType.RELOCATION
        expl = f"paragraph {c.original} was relocated to paragraph {c.revised}" + (
            f" (similarity {c.similarity:.0%})" if c.similarity < 1 else ""
        )
    elif c.kind is ChangeKind.MODIFIED:
        etype = EvidenceType.HEADING_CHANGED if c.is_heading else EvidenceType.TEXT_MODIFIED
        expl = f"paragraph {c.original} was modified (similarity {c.similarity:.0%})"
    else:
        etype = EvidenceType.NO_CHANGE
        expl = f"paragraph {c.original} is unchanged"
    if c.is_caption and c.kind is not ChangeKind.UNCHANGED:
        info = caption_info(c.revised_text or c.original_text)
        if info and info[0] == "figure":
            etype = EvidenceType.FIGURE_CHANGED
    return Evidence(
        type=etype,
        direction=direction,
        original_location=o_loc,
        revised_location=r_loc,
        original_text=truncate(c.original_text, EXCERPT) if c.original_text else None,
        revised_text=truncate(c.revised_text, EXCERPT) if c.revised_text else None,
        similarity=c.similarity if c.original and c.revised else None,
        explanation=expl + (f"; {note}" if note else ""),
    )


def ev_section_growth(
    ctx: AuditContext, scope: Scope, target: Target, direction: EvidenceDirection
) -> Evidence | None:
    if scope.whole_document or target.original_section is None or target.revised_section is None:
        return None
    sec = target.revised_section
    delta = scope.words_after - scope.words_before
    verb = "grew" if delta > 0 else "shrank" if delta < 0 else "stayed at"
    label = sec.heading or "front matter"
    if delta == 0:
        explanation = f"{label} has {scope.words_after} words in both versions"
    else:
        explanation = (
            f"{label} {verb} from {scope.words_before} to {scope.words_after} words ({delta:+d})"
        )
    et = (
        EvidenceType.TEXT_ADDED
        if delta > 0
        else EvidenceType.TEXT_REMOVED
        if delta < 0
        else EvidenceType.NO_CHANGE
    )
    return Evidence(
        type=et,
        direction=direction,
        explanation=explanation,
    )


def similar_count(
    ctx: AuditContext, doc: Document, text: str, section_ids: set[str] | None, thr: float = 0.92
) -> list[int]:
    out = []
    for p in doc.paragraphs:
        if p.is_heading or (section_ids is not None and p.section_id not in section_ids):
            continue
        if (
            char_similarity(text, p.text) >= thr
            if len(text) < 60
            else similarity(text, p.text) >= thr
        ):
            out.append(p.number)
    return out

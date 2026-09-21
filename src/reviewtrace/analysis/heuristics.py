"""Deterministic, explainable assessment rules.

Each rule inspects the diff for *evidence* and returns an :class:`Assessment`. The rules
are intentionally cautious:

* RESOLVED needs supporting evidence that the requested thing now exists (or that the
  unwanted thing is gone), found where the request points.
* UNRESOLVED needs evidence that the request was not acted on.
* Anything that turns on whether new text is *good enough* is NEEDS_REVIEW, because that
  is a semantic judgement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from reviewtrace.analysis.confidence import (
    RuleStrength,
    TargetQuality,
    combine_confidence,
    decide,
    requires_human_review,
)
from reviewtrace.analysis.evidence_finder import (
    AddedUnit,
    AuditContext,
    Scope,
    Target,
    added_units,
    build_scope,
    changes_for_paragraphs,
    ev_added,
    ev_change,
    ev_section_growth,
    locate_target,
    similar_count,
)
from reviewtrace.analysis.request_spec import RequestSpec, parse_request
from reviewtrace.extractors.docx import caption_info
from reviewtrace.models.diff import ChangeKind, TableChange, TableChangeKind
from reviewtrace.models.document import Location, TrackedKind
from reviewtrace.models.evidence import Evidence, EvidenceDirection, EvidenceType
from reviewtrace.models.finding import Confidence, Finding, Status
from reviewtrace.models.issue import IssueCategory, ReviewIssue
from reviewtrace.utils.text import (
    contains_number,
    normalize,
    numbers_in,
    sentences,
    similarity,
    stem,
    stems_in,
    tokens,
    truncate,
    word_count,
)

C = IssueCategory
S, X, CTX = EvidenceDirection.SUPPORTS, EvidenceDirection.CONTRADICTS, EvidenceDirection.CONTEXT
RS = RuleStrength

MIN_WORDS = {C.ADD: 8, C.CLARIFY: 8, C.EXPLAIN: 25, C.EXPAND: 40}
EXPAND_MIN_GROWTH = 0.15
_DUP_WORDS = re.compile(
    r"\b(duplicat\w*|repeat\w*|redundan\w*|twice|same paragraph|identical)\b", re.I
)
_CITE_PAT = re.compile(
    r"\b([A-Z][A-Za-z'\-]{2,})(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][A-Za-z'\-]+)?,?\s*\(?((?:19|20)\d{2})[a-z]?\)?"
)
_NOT_AUTHORS = {
    "Section",
    "Table",
    "Figure",
    "Since",
    "After",
    "From",
    "Published",
    "Before",
    "Between",
    "Chapter",
}
_TABLE_ADD = re.compile(r"\b(add|include|insert|provide|create|present|show)\b", re.I)


@dataclass
class Assessment:
    status: Status
    strength: RuleStrength
    rationale: str
    method: str
    evidence: list[Evidence] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    remaining: str | None = None
    quality: TargetQuality | None = None
    changed: bool | None = None  # overrides the scope-based "document changed" observation


# =================================================================================
# helpers
# =================================================================================


def _no_change_evidence(
    ctx: AuditContext, target: Target, scope: Scope, note: str = ""
) -> list[Evidence]:
    out: list[Evidence] = []
    if target.original_paragraphs:
        for c in changes_for_paragraphs(ctx, target.original_paragraphs)[:3]:
            out.append(ev_change(ctx, c, X, note))
    growth = ev_section_growth(ctx, scope, target, X)
    if growth is not None and not out:
        out.append(growth)
    if not out:
        out.append(
            Evidence(
                type=EvidenceType.NO_CHANGE,
                direction=X,
                explanation="no relevant change was found in the revised document"
                + (f"; {note}" if note else ""),
            )
        )
    return out


def _table_text(rows: list[list[str]]) -> str:
    return "\n".join(" | ".join(r) for r in rows)


def _find_table_change(ctx: AuditContext, index: int) -> TableChange | None:
    return next((t for t in ctx.diff.tables if t.original_index == index), None)


def _original_scope_text(ctx: AuditContext, target: Target) -> str:
    if target.original_section is not None:
        text = ctx.original.section_text(target.original_section.id, subtree=target.subtree)
        para = " ".join(ctx.original.text_of(n) for n in target.original_paragraphs)
        return f"{text}\n{para}"
    return ctx.original.full_text()


def _matched_words(text: str, stem_set: set[str]) -> list[str]:
    seen: list[str] = []
    for tok in tokens(text):
        if stem(tok) in stem_set and tok not in seen:
            seen.append(tok)
    return seen


# =================================================================================
# REMOVE
# =================================================================================


def _remove(text: str, spec: RequestSpec, target: Target, ctx: AuditContext) -> Assessment:
    if spec.table_refs and target.table is not None:
        change = _find_table_change(ctx, target.table.index)
        label = target.table.label or f"Table #{target.table.index + 1}"
        if change is not None and change.kind is TableChangeKind.REMOVED:
            ev = Evidence(
                type=EvidenceType.TABLE_CHANGED,
                direction=S,
                original_location=Location(table_index=target.table.index),
                explanation=f"{label} no longer exists in the revised document",
            )
            return Assessment(
                Status.RESOLVED, RS.DETERMINISTIC, f"{label} was removed.", "table-removal", [ev]
            )
        return Assessment(
            Status.UNRESOLVED,
            RS.DETERMINISTIC,
            f"{label} is still present in the revised document.",
            "table-removal",
            [
                Evidence(
                    type=EvidenceType.NO_CHANGE,
                    direction=X,
                    original_location=Location(table_index=target.table.index),
                    explanation=f"{label} is still present"
                    + ("" if change is None else f" ({change.describe()})"),
                )
            ],
            remaining=f"Remove {label}.",
        )
    if _DUP_WORDS.search(text):
        return _remove_duplicates(text, target, ctx)
    if not target.original_paragraphs:
        return Assessment(
            Status.NOT_ASSESSABLE,
            RS.STRUCTURAL,
            "The comment asks for something to be removed but does not identify what "
            "(no anchor text, quoted phrase or section was available).",
            "removal-no-target",
        )
    return _remove_anchored(target, ctx)


def _remove_anchored(target: Target, ctx: AuditContext) -> Assessment:
    orig, rev = ctx.original, ctx.revised
    removed_ev: list[Evidence] = []
    present_ev: list[Evidence] = []
    rewritten_ev: list[Evidence] = []
    still: list[int] = []
    for p in target.original_paragraphs:
        para_text = orig.text_of(p)
        anchor = target.anchor_text or para_text
        whole = len(normalize(anchor)) >= 0.9 * len(normalize(para_text))
        ch = ctx.diff.change_for_original(p)
        if ch is None:
            continue
        c_o = ctx.count_text(orig, anchor)
        c_r = ctx.count_text(rev, anchor)
        if ch.kind is ChangeKind.REMOVED:
            removed_ev.append(
                ev_change(ctx, ch, S, "no counterpart remains in the revised document")
            )
        elif ch.kind is ChangeKind.MOVED and c_r >= max(c_o, 1):
            present_ev.append(ev_change(ctx, ch, X, "the text was relocated, not removed"))
            still.append(p)
        elif c_o and c_r < c_o:
            if whole and ch.kind is ChangeKind.MODIFIED and ch.similarity >= 0.5:
                rewritten_ev.append(
                    ev_change(ctx, ch, CTX, "the paragraph was rewritten rather than removed")
                )
            else:
                removed_ev.append(
                    ev_change(
                        ctx,
                        ch,
                        S,
                        f"the anchored text no longer appears (occurrences {c_o} → {c_r})",
                    )
                )
        elif c_o and c_r >= c_o:
            where = ctx.rev_loc(ctx.find_in(rev, anchor)[:3])
            present_ev.append(
                Evidence(
                    type=EvidenceType.EXACT_MATCH,
                    direction=X,
                    original_location=ctx.orig_loc(p),
                    revised_location=where,
                    original_text=truncate(anchor, 300),
                    revised_text=truncate(anchor, 300),
                    similarity=1.0,
                    explanation=f"the anchored text still appears in the revised document at {where.label() if where else 'an unlocated position'}",
                )
            )
            still.append(p)
        else:  # anchor spans paragraphs or is not a literal substring: fall back to fuzzy check
            counterpart = ctx.revised.text_of(ch.revised) if ch.revised else ""
            best = max((similarity(anchor, s) for s in sentences(counterpart)), default=0.0)
            if ch.kind is ChangeKind.UNCHANGED or best >= 0.85:
                present_ev.append(ev_change(ctx, ch, X, "the anchored text is still present"))
                still.append(p)
            elif ch.kind is ChangeKind.MODIFIED:
                removed_ev.append(
                    ev_change(ctx, ch, S, "the anchored sentence is no longer in the paragraph")
                )

    tracked = [
        t
        for t in ctx.revised.tracked_changes
        if t.kind is TrackedKind.DELETION
        and len(t.text) >= 8
        and (
            normalize(t.text)
            in normalize(
                target.anchor_text or " ".join(orig.text_of(n) for n in target.original_paragraphs)
            )
        )
    ]
    if removed_ev:
        for t in tracked[:2]:
            removed_ev.append(
                Evidence(
                    type=EvidenceType.TRACKED_REVISION,
                    direction=S,
                    original_text=truncate(t.text, 300),
                    explanation=f"tracked deletion by {t.author or 'unknown'} (still pending in the file)",
                )
            )

    if rewritten_ev:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            "The anchored paragraph was rewritten rather than removed; whether the rewrite satisfies the request needs review.",
            "removal-rewritten",
            rewritten_ev + removed_ev,
        )
    if removed_ev and not present_ev:
        return Assessment(
            Status.RESOLVED,
            RS.DETERMINISTIC,
            "The anchored text was removed from the revised document.",
            "removal-check",
            removed_ev,
        )
    if present_ev and not removed_ev:
        return Assessment(
            Status.UNRESOLVED,
            RS.DETERMINISTIC,
            "The text the reviewer asked to remove is still present in the revised document.",
            "removal-check",
            present_ev,
            remaining="Remove the anchored text.",
        )
    if removed_ev and present_ev:
        return Assessment(
            Status.PARTIALLY_RESOLVED,
            RS.DETERMINISTIC,
            "Some of the anchored text was removed but part of it is still present.",
            "removal-check",
            removed_ev + present_ev,
            missing=[f"paragraph {p} still present" for p in still],
            remaining="Remove the remaining anchored text.",
        )
    return Assessment(
        Status.NEEDS_REVIEW,
        RS.STRUCTURAL,
        "The anchored paragraph could not be matched to the revised document.",
        "removal-unmatched",
    )


def _remove_duplicates(text: str, target: Target, ctx: AuditContext) -> Assessment:
    orig, rev = ctx.original, ctx.revised
    section_ids: set[str] | None = None
    if target.original_section is not None and not target.section_removed:
        section_ids = (
            set(orig.subtree_ids(target.original_section.id))
            if target.subtree
            else {target.original_section.id}
        )
    rev_ids: set[str] | None = None
    if target.revised_section is not None:
        rev_ids = (
            set(rev.subtree_ids(target.revised_section.id))
            if target.subtree
            else {target.revised_section.id}
        )

    groups: list[list[int]] = []
    if target.original_paragraphs:
        base = orig.text_of(target.original_paragraphs[0])
        g = similar_count(ctx, orig, base, section_ids, 0.95)
        if len(g) >= 2:
            groups.append(g)
    else:
        seen: set[int] = set()
        for p in orig.paragraphs:
            if p.is_heading or p.number in seen or len(p.text) < 25:
                continue
            if section_ids is not None and p.section_id not in section_ids:
                continue
            g = similar_count(ctx, orig, p.text, section_ids, 0.95)
            if len(g) >= 2:
                groups.append(g)
                seen.update(g)
    if not groups:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            "The comment refers to duplicated text, but no duplicated paragraph was found in the original "
            "at the indicated location.",
            "duplicate-not-found",
        )

    evidence: list[Evidence] = []
    reduced = still_dup = 0
    missing: list[str] = []
    for g in groups:
        base = orig.text_of(g[0])
        after = similar_count(ctx, rev, base, rev_ids, 0.95)
        if len(after) < len(g):
            reduced += 1
            for n in g:
                ch = ctx.diff.change_for_original(n)
                if ch is not None and ch.kind is ChangeKind.REMOVED:
                    evidence.append(ev_change(ctx, ch, S, "duplicate copy removed"))
            if not any(e.direction is S for e in evidence):
                evidence.append(
                    Evidence(
                        type=EvidenceType.TEXT_REMOVED,
                        direction=S,
                        original_location=ctx.orig_loc(g),
                        explanation=f"identical copies fell from {len(g)} to {len(after)}",
                    )
                )
            if len(after) >= 2:
                missing.append(f"{len(after)} identical copies of paragraph {g[0]} remain")
        else:
            still_dup += 1
            loc = ctx.rev_loc(after)
            evidence.append(
                Evidence(
                    type=EvidenceType.EXACT_MATCH,
                    direction=X,
                    original_location=ctx.orig_loc(g),
                    revised_location=loc,
                    original_text=truncate(base, 300),
                    similarity=1.0,
                    explanation=(
                        f"the duplicated paragraph ({len(g)} copies at "
                        f"{ctx.orig_loc(g).label() if ctx.orig_loc(g) else 'unknown'}) still appears "  # type: ignore[union-attr]
                        f"{len(after)} times in the revised document"
                        + (f" ({loc.label()})" if loc else "")
                    ),
                )
            )
            missing.append(f"duplicate of paragraph {g[0]} still present")
    if reduced and not still_dup and not missing:
        return Assessment(
            Status.RESOLVED,
            RS.DETERMINISTIC,
            "The duplicated text now appears only once.",
            "duplicate-check",
            evidence,
        )
    if still_dup and not reduced:
        return Assessment(
            Status.UNRESOLVED,
            RS.DETERMINISTIC,
            "The duplicated paragraph was not removed: identical copies are still present in the revised document.",
            "duplicate-check",
            evidence,
            missing=missing,
            remaining="Remove the repeated paragraph.",
        )
    return Assessment(
        Status.PARTIALLY_RESOLVED,
        RS.DETERMINISTIC,
        "Duplicate text was reduced but is not fully removed.",
        "duplicate-check",
        evidence,
        missing=missing,
        remaining="Remove the remaining duplicate text.",
    )


# =================================================================================
# REPLACE / CORRECT (headings, text, numbers)
# =================================================================================


def _heading_rename(old: str, new: str, ctx: AuditContext) -> Assessment | None:
    orig = ctx.original
    n_old = normalize(old)
    sec = next(
        (
            s
            for s in orig.sections[1:]
            if normalize(s.title) == n_old or normalize(s.heading) == n_old
        ),
        None,
    )
    if sec is None:
        return None
    match = ctx.diff.section_for_original(sec.id)
    if match is None or match.revised_id is None:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            f"The section headed '{sec.heading}' no longer exists in the revised document, so the rename cannot be checked.",
            "heading-change",
            [
                Evidence(
                    type=EvidenceType.SECTION_REMOVED,
                    direction=CTX,
                    original_location=ctx.section_loc(orig, sec),
                    explanation=f"section '{sec.heading}' has no counterpart in the revised document",
                )
            ],
            quality=TargetQuality.EXACT,
        )
    rev_sec = ctx.revised.section(match.revised_id)
    assert rev_sec is not None
    n_new = normalize(new)
    ev = Evidence(
        type=EvidenceType.HEADING_CHANGED,
        direction=CTX,
        original_location=ctx.section_loc(orig, sec),
        revised_location=ctx.section_loc(ctx.revised, rev_sec),
        original_text=sec.heading,
        revised_text=rev_sec.heading,
        similarity=match.heading_similarity,
        explanation=f"heading '{sec.heading}' now reads '{rev_sec.heading}'",
    )
    if normalize(rev_sec.title) == n_new or normalize(rev_sec.heading) == n_new:
        ev.direction = S
        return Assessment(
            Status.RESOLVED,
            RS.DETERMINISTIC,
            f"The heading was changed to '{new}'.",
            "heading-change",
            [ev],
            quality=TargetQuality.EXACT,
        )
    if normalize(rev_sec.title) == n_old or normalize(rev_sec.heading) == n_old:
        ev.direction = X
        ev.type = EvidenceType.NO_CHANGE
        ev.explanation = f"heading still reads '{rev_sec.heading}'"
        return Assessment(
            Status.UNRESOLVED,
            RS.DETERMINISTIC,
            f"The heading still reads '{old}'.",
            "heading-change",
            [ev],
            remaining=f"Change the heading to '{new}'.",
            quality=TargetQuality.EXACT,
        )
    return Assessment(
        Status.NEEDS_REVIEW,
        RS.STRUCTURAL,
        f"The heading was changed, but to '{rev_sec.heading}' rather than the requested '{new}'.",
        "heading-change",
        [ev],
        quality=TargetQuality.EXACT,
    )


def _pair_replace(
    old: str | None,
    new: str,
    numeric: bool,
    target: Target,
    ctx: AuditContext,
    label: str,
) -> Assessment:
    orig, rev = ctx.original, ctx.revised

    def count(doc_texts: list[str], needle: str) -> int:
        if numeric:
            return sum(contains_number(t, needle) for t in doc_texts)
        n = normalize(needle)
        pat = re.compile(r"(?<![\w])" + re.escape(n) + r"(?![\w])")
        return sum(len(pat.findall(normalize(t))) for t in doc_texts)

    quality = target.quality
    scope_desc = "the document"
    o_paras: list[int] = []
    r_paras: list[int] = []
    if target.table is not None:
        change = _find_table_change(ctx, target.table.index)
        o_texts = [_table_text(target.table.rows)]
        r_table = (
            rev.tables[change.revised_index]
            if change and change.revised_index is not None
            else None
        )
        r_texts = [_table_text(r_table.rows)] if r_table else []
        scope_desc = target.table.label or f"table #{target.table.index + 1}"
    elif target.original_paragraphs:
        o_paras = target.original_paragraphs
        for p in o_paras:
            ch = ctx.diff.change_for_original(p)
            if ch is not None and ch.revised is not None:
                r_paras.append(ch.revised)
        o_texts = [orig.text_of(n) for n in o_paras]
        r_texts = [rev.text_of(n) for n in r_paras]
        anchor_loc = ctx.orig_loc(o_paras)
        scope_desc = anchor_loc.label() if anchor_loc else "the anchored paragraph"
    elif (
        target.original_section is not None
        and not target.section_removed
        and target.revised_section is not None
    ):
        o_ids = set(orig.subtree_ids(target.original_section.id))
        r_ids = set(rev.subtree_ids(target.revised_section.id))
        o_texts = [p.text for p in orig.paragraphs if p.section_id in o_ids]
        r_texts = [p.text for p in rev.paragraphs if p.section_id in r_ids]
        scope_desc = target.original_section.heading or "the section"
    else:
        o_texts = [p.text for p in orig.paragraphs]
        r_texts = [p.text for p in rev.paragraphs]

    strength = RS.DETERMINISTIC
    if old is None:
        cands = {n for t in o_texts for n in numbers_in(t)} - {new}
        if len(cands) == 1:
            old = next(iter(cands))
        else:
            n_o, n_r = count(o_texts, new), count(r_texts, new)
            if n_r > n_o:
                ev = Evidence(
                    type=EvidenceType.TEXT_MODIFIED,
                    direction=S,
                    revised_location=ctx.rev_loc(r_paras) if r_paras else None,
                    explanation=f"the requested {label} '{new}' now appears in {scope_desc} ({n_o} → {n_r} occurrences); "
                    "the original value could not be identified",
                )
                return Assessment(
                    Status.RESOLVED,
                    RS.LEXICAL,
                    f"The requested {label} '{new}' is now present.",
                    "value-check",
                    [ev],
                )
            return Assessment(
                Status.UNRESOLVED,
                RS.LEXICAL,
                f"The requested {label} '{new}' does not appear in the revised text.",
                "value-check",
                _no_change_evidence(ctx, target, build_scope(ctx, target)),
                remaining=f"Change the {label} to '{new}'.",
            )

    c_o, c_r = count(o_texts, old), count(r_texts, old)
    n_o, n_r = count(o_texts, new), count(r_texts, new)
    where_r = ctx.rev_loc(ctx.find_number(rev, old) if numeric else ctx.find_in(rev, old)[:5])
    if c_o == 0:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            f"The {label} '{old}' named in the comment was not found in {scope_desc} of the original, so the change cannot be verified.",
            "value-check",
            quality=quality,
        )
    if c_r == 0 and n_r > n_o:
        ev = Evidence(
            type=EvidenceType.TEXT_MODIFIED,
            direction=S,
            original_location=ctx.orig_loc(o_paras) if o_paras else None,
            revised_location=ctx.rev_loc(r_paras) if r_paras else None,
            original_text=truncate(
                next((t for t in o_texts if old and normalize(old) in normalize(t)), ""), 300
            )
            or None,
            revised_text=truncate(
                next((t for t in r_texts if normalize(new) in normalize(t)), ""), 300
            )
            or None,
            explanation=f"'{old}' no longer appears in {scope_desc} and '{new}' now does ({n_o} → {n_r} occurrences)",
        )
        return Assessment(
            Status.RESOLVED, strength, f"'{old}' was replaced by '{new}'.", "value-check", [ev]
        )
    if c_r == 0:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            f"'{old}' no longer appears, but '{new}' was not found either; the text was changed in some other way.",
            "value-check",
            _no_change_evidence(ctx, target, build_scope(ctx, target)),
            quality=quality,
        )
    if c_r < c_o and n_r > n_o:
        partial_ev = [
            Evidence(
                type=EvidenceType.TEXT_MODIFIED,
                direction=S,
                revised_location=ctx.rev_loc(r_paras) if r_paras else None,
                explanation=f"'{old}' was replaced in {c_o - c_r} of {c_o} places in {scope_desc}",
            ),
            Evidence(
                type=EvidenceType.EXACT_MATCH,
                direction=X,
                revised_location=where_r,
                explanation=f"'{old}' still appears {c_r} time(s)"
                + (f" at {where_r.label()}" if where_r else ""),
            ),
        ]
        return Assessment(
            Status.PARTIALLY_RESOLVED,
            strength,
            f"'{old}' was replaced in some places but remains in others.",
            "value-check",
            partial_ev,
            missing=[f"'{old}' still appears {c_r} time(s)"],
            remaining=f"Replace the remaining occurrences of '{old}' with '{new}'.",
        )
    ev = Evidence(
        type=EvidenceType.EXACT_MATCH,
        direction=X,
        original_location=ctx.orig_loc(o_paras) if o_paras else None,
        revised_location=where_r,
        similarity=1.0,
        explanation=f"'{old}' still appears in {scope_desc} of the revised document"
        + (f" ({where_r.label()})" if where_r else ""),
    )
    return Assessment(
        Status.UNRESOLVED,
        strength,
        f"'{old}' was not replaced by '{new}'.",
        "value-check",
        [ev],
        remaining=f"Replace '{old}' with '{new}'.",
    )


def _replace_or_correct(
    text: str, spec: RequestSpec, target: Target, ctx: AuditContext, category: IssueCategory
) -> Assessment:
    if spec.replace_pair:
        old, new = spec.replace_pair
        head = _heading_rename(old, new, ctx)
        if head is not None:
            return head
        return _pair_replace(old, new, False, target, ctx, "text")
    if spec.numeric_correction:
        old_n, new_n = spec.numeric_correction
        return _pair_replace(old_n, new_n, True, target, ctx, "value")
    if category is C.CORRECT and spec.table_refs:
        return _table(text, spec, target, ctx)
    return _generic_change(text, spec, target, ctx, category)


# =================================================================================
# TABLE / FIGURE
# =================================================================================


def _table(text: str, spec: RequestSpec, target: Target, ctx: AuditContext) -> Assessment:
    table = target.table
    if (
        table is None
        and target.original_section is not None
        and len(target.original_section.table_indices) == 1
    ):
        table = ctx.original.tables[target.original_section.table_indices[0]]
        target.table = table
    if spec.replace_pair or spec.numeric_correction:
        if table is None:
            return Assessment(
                Status.NOT_ASSESSABLE,
                RS.STRUCTURAL,
                "The comment concerns a specific table value, but the table could not be identified.",
                "table-value",
            )
        if spec.replace_pair:
            return _pair_replace(
                spec.replace_pair[0], spec.replace_pair[1], False, target, ctx, "text"
            )
        assert spec.numeric_correction
        return _pair_replace(
            spec.numeric_correction[0], spec.numeric_correction[1], True, target, ctx, "value"
        )

    adding = bool(_TABLE_ADD.search(text)) and not spec.table_refs
    if adding and table is None:
        added = [t for t in ctx.diff.tables if t.kind is TableChangeKind.ADDED]
        if not added:
            return Assessment(
                Status.UNRESOLVED,
                RS.DETERMINISTIC,
                "No table was added to the revised document.",
                "table-added",
                [
                    Evidence(
                        type=EvidenceType.NO_CHANGE,
                        direction=X,
                        explanation="the revised document contains no new table",
                    )
                ],
                remaining="Add the requested table.",
                quality=TargetQuality.EXACT,
            )
        ev = [
            Evidence(
                type=EvidenceType.TABLE_CHANGED,
                direction=CTX,
                revised_location=Location(table_index=t.revised_index),
                revised_text=t.revised_caption,
                explanation=f"table #{(t.revised_index or 0) + 1} was added"
                + (f" ({t.revised_caption})" if t.revised_caption else ""),
            )
            for t in added[:3]
        ]
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            "A new table exists in the revised document, but whether it contains what the reviewer asked for needs review.",
            "table-added",
            ev,
            quality=TargetQuality.EXACT,
        )
    if table is None:
        what = spec.table_refs[0] if spec.table_refs else "the table"
        return Assessment(
            Status.NOT_ASSESSABLE,
            RS.STRUCTURAL,
            f"{what} could not be identified in the original document.",
            "table-lookup",
        )
    label = table.label or f"table #{table.index + 1}"
    change = _find_table_change(ctx, table.index)
    orig_loc = Location(section_id=table.section_id, table_index=table.index)
    if change is None or change.kind is TableChangeKind.UNCHANGED:
        return Assessment(
            Status.UNRESOLVED,
            RS.DETERMINISTIC,
            f"{label} is identical in the revised document.",
            "table-diff",
            [
                Evidence(
                    type=EvidenceType.NO_CHANGE,
                    direction=X,
                    original_location=orig_loc,
                    explanation=f"{label} has the same rows, columns, cells and caption in both versions",
                )
            ],
            remaining=f"Revise {label}.",
            quality=TargetQuality.EXACT,
        )
    if change.kind is TableChangeKind.REMOVED:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            f"{label} no longer exists in the revised document.",
            "table-diff",
            [
                Evidence(
                    type=EvidenceType.TABLE_CHANGED,
                    direction=CTX,
                    original_location=orig_loc,
                    explanation=f"{label} was removed",
                )
            ],
            quality=TargetQuality.EXACT,
        )
    ev = [
        Evidence(
            type=EvidenceType.TABLE_CHANGED,
            direction=CTX,
            original_location=orig_loc,
            revised_location=Location(table_index=change.revised_index),
            explanation=f"{label}: {change.describe()}",
        )
    ]
    for cc in change.cell_changes[:4]:
        ev.append(
            Evidence(
                type=EvidenceType.TABLE_CHANGED,
                direction=CTX,
                original_location=Location(table_index=table.index, cell=(cc.row, cc.col)),
                original_text=cc.original,
                revised_text=cc.revised,
                explanation=f"{cc.row_label} / {cc.col_label}: '{cc.original}' → '{cc.revised}'",
            )
        )
    return Assessment(
        Status.NEEDS_REVIEW,
        RS.STRUCTURAL,
        f"{label} was changed ({change.describe()}); whether the change is the correction the reviewer wanted needs review.",
        "table-diff",
        ev,
        quality=TargetQuality.EXACT,
    )


def _figure(spec: RequestSpec, target: Target, ctx: AuditContext) -> Assessment:
    n = target.figure_paragraph or (
        target.original_paragraphs[0] if target.original_paragraphs else None
    )
    label = spec.figure_refs[0] if spec.figure_refs else "the figure"
    if n is None or caption_info(ctx.original.text_of(n)) is None:
        return Assessment(
            Status.NOT_ASSESSABLE,
            RS.STRUCTURAL,
            f"{label} could not be identified in the original document, and figure content is not compared in this version.",
            "figure-lookup",
        )
    ch = ctx.diff.change_for_original(n)
    if ch is None or ch.kind is ChangeKind.REMOVED:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            f"The caption of {label} no longer exists in the revised document.",
            "figure-diff",
            [ev_change(ctx, ch, CTX)] if ch else [],
            quality=TargetQuality.EXACT,
        )
    if ch.kind is ChangeKind.UNCHANGED:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            f"The caption of {label} is unchanged. Image content is not compared, so an updated figure cannot be ruled out.",
            "figure-diff",
            [ev_change(ctx, ch, CTX, "image content is not compared")],
            quality=TargetQuality.FUZZY,
        )
    return Assessment(
        Status.NEEDS_REVIEW,
        RS.STRUCTURAL,
        f"The caption of {label} changed; whether the figure now satisfies the reviewer needs review.",
        "figure-diff",
        [ev_change(ctx, ch, CTX)],
        quality=TargetQuality.EXACT,
    )


# =================================================================================
# REFERENCES / CITATIONS
# =================================================================================


def _reference(
    text: str, spec: RequestSpec, target: Target, ctx: AuditContext, category: IssueCategory
) -> Assessment:
    orig, rev, refs = ctx.original, ctx.revised, ctx.diff.references
    if spec.style_name:
        return Assessment(
            Status.NOT_ASSESSABLE,
            RS.STRUCTURAL,
            f"Compliance with {spec.style_name.upper()} formatting is not verified by ReviewTrace; "
            "a dedicated citation-style checker is needed.",
            "citation-style",
        )
    cite = next(
        (
            (m.group(1), m.group(2))
            for m in _CITE_PAT.finditer(text)
            if m.group(1) not in _NOT_AUTHORS
        ),
        None,
    )
    if cite and category in (C.CITE, C.REFERENCE, C.ADD, C.UPDATE):
        return _named_citation(cite, ctx)

    if not rev.references and not orig.references:
        return Assessment(
            Status.NOT_ASSESSABLE,
            RS.STRUCTURAL,
            "No reference list was detected in either document (a heading such as 'References' is needed).",
            "reference-diff",
        )
    stats = (
        f"reference list: {refs.original_count} → {refs.revised_count} entries; "
        f"newest year {refs.newest_original or 'n/a'} → {refs.newest_revised or 'n/a'}"
    )
    added_ev = [
        Evidence(
            type=EvidenceType.REFERENCE_ADDED,
            direction=CTX,
            revised_text=truncate(a, 300),
            explanation=f"reference added{f' ({y})' if y else ''}: {truncate(a, 90)}",
        )
        for a, y in zip(refs.added[:5], refs.added_years[:5], strict=False)
    ]
    if spec.has_explicit_reference_criteria:
        lo = spec.since_year or 0
        hi = spec.until_year or 9999
        need = spec.min_count or 1
        qualifying = [
            (a, y)
            for a, y in zip(refs.added, refs.added_years, strict=False)
            if y and lo <= y <= hi
        ]
        got = len(qualifying)
        crit = f"at least {need} added reference(s)" + (
            f" from {spec.since_year}" if spec.since_year else ""
        )
        if got >= need:
            for e in added_ev:
                e.direction = S
            return Assessment(
                Status.RESOLVED,
                RS.DETERMINISTIC,
                f"{got} qualifying reference(s) were added; the comment asked for {crit}.",
                "reference-criteria",
                added_ev,
                quality=TargetQuality.EXACT,
            )
        if got > 0:
            for e in added_ev:
                e.direction = S
            return Assessment(
                Status.PARTIALLY_RESOLVED,
                RS.DETERMINISTIC,
                f"Only {got} of the {need} requested references were added.",
                "reference-criteria",
                added_ev,
                missing=[f"{need - got} more qualifying reference(s)"],
                remaining=f"Add {need - got} more reference(s) meeting the stated criteria.",
                quality=TargetQuality.EXACT,
            )
        return Assessment(
            Status.UNRESOLVED,
            RS.DETERMINISTIC,
            f"No added reference meets the stated criteria ({crit}).",
            "reference-criteria",
            [Evidence(type=EvidenceType.NO_CHANGE, direction=X, explanation=stats), *added_ev],
            remaining=f"Add references meeting the criteria ({crit}).",
            quality=TargetQuality.EXACT,
        )
    if not refs.changed:
        return Assessment(
            Status.UNRESOLVED,
            RS.DETERMINISTIC,
            "The reference list is unchanged in the revised document.",
            "reference-diff",
            [Evidence(type=EvidenceType.NO_CHANGE, direction=X, explanation=stats)],
            remaining="Update the reference list.",
            quality=TargetQuality.EXACT,
        )
    ev = [Evidence(type=EvidenceType.REFERENCE_ADDED, direction=CTX, explanation=stats), *added_ev]
    if refs.modified:
        ev.append(
            Evidence(
                type=EvidenceType.TEXT_MODIFIED,
                direction=CTX,
                explanation=f"{len(refs.modified)} reference(s) modified",
            )
        )
    if refs.removed:
        ev.append(
            Evidence(
                type=EvidenceType.TEXT_REMOVED,
                direction=CTX,
                explanation=f"{len(refs.removed)} reference(s) removed",
            )
        )
    return Assessment(
        Status.NEEDS_REVIEW,
        RS.STRUCTURAL,
        f"The reference list changed ({len(refs.added)} added, {len(refs.removed)} removed, {len(refs.modified)} modified). "
        "The comment gives no objective criterion, so whether this is sufficient needs review.",
        "reference-diff",
        ev,
        quality=TargetQuality.EXACT,
    )


def _named_citation(cite: tuple[str, str], ctx: AuditContext) -> Assessment:
    author, year = cite
    pat = re.compile(rf"\b{re.escape(author)}\b", re.I)

    def in_list(doc_refs: list) -> bool:  # type: ignore[type-arg]
        return any(pat.search(r.raw) and str(year) in r.raw for r in doc_refs)

    def in_text(doc: object) -> bool:
        text = "\n".join(p.text for p in doc.paragraphs if not _is_ref_para(doc, p.number))  # type: ignore[attr-defined]
        return bool(re.search(rf"\b{re.escape(author)}\b[^.\n]{{0,40}}{year}", text, re.I))

    ref_o, ref_r = in_list(ctx.original.references), in_list(ctx.revised.references)
    txt_o, txt_r = in_text(ctx.original), in_text(ctx.revised)
    label = f"{author} ({year})"
    ref_new, txt_new = ref_r and not ref_o, txt_r and not txt_o
    ev: list[Evidence] = []
    if ref_new:
        ev.append(
            Evidence(
                type=EvidenceType.REFERENCE_ADDED,
                direction=S,
                explanation=f"{label} was added to the reference list",
            )
        )
    if txt_new:
        ev.append(
            Evidence(
                type=EvidenceType.TEXT_ADDED,
                direction=S,
                explanation=f"an in-text citation of {label} was added",
            )
        )
    if ref_new and txt_new:
        return Assessment(
            Status.RESOLVED,
            RS.DETERMINISTIC,
            f"{label} is now cited in the text and listed in the references.",
            "named-citation",
            ev,
            quality=TargetQuality.SECTION,
        )
    if ref_new or txt_new:
        miss = "in-text citation" if ref_new else "reference-list entry"
        return Assessment(
            Status.PARTIALLY_RESOLVED,
            RS.DETERMINISTIC,
            f"{label} was added in only one place.",
            "named-citation",
            ev,
            missing=[f"{miss} for {label}"],
            remaining=f"Add the {miss} for {label}.",
            quality=TargetQuality.EXACT,
        )
    if ref_r or txt_r:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            f"{label} already appeared in the original document, so no new citation was added.",
            "named-citation",
            [
                Evidence(
                    type=EvidenceType.EXACT_MATCH,
                    direction=CTX,
                    explanation=f"{label} appears in both versions",
                )
            ],
        )
    return Assessment(
        Status.UNRESOLVED,
        RS.DETERMINISTIC,
        f"{label} does not appear in the revised document.",
        "named-citation",
        [
            Evidence(
                type=EvidenceType.NO_CHANGE,
                direction=X,
                explanation=f"no reference or in-text citation matching {label} was found",
            )
        ],
        remaining=f"Cite {label}.",
        quality=TargetQuality.EXACT,
    )


def _is_ref_para(doc: object, number: int) -> bool:
    return any(r.paragraph == number for r in doc.references)  # type: ignore[attr-defined]


# =================================================================================
# ADD / EXPAND / EXPLAIN / CLARIFY
# =================================================================================


def _additive(
    text: str,
    category: IssueCategory,
    spec: RequestSpec,
    target: Target,
    ctx: AuditContext,
    subjective: bool,
) -> Assessment:
    scope = build_scope(ctx, target)
    units = added_units(scope)
    added_words = sum(word_count(u.text) for u in units)
    orig_text = _original_scope_text(ctx, target)
    orig_stems = stems_in(orig_text + " " + target.anchor_text)
    orig_norm = normalize(orig_text)
    words_of = {stem(tok): tok for tok in tokens(text)}
    # Words that merely name the target section ("limitation" in "Limitations") are topic, not content.
    heading_stems = (
        stems_in(target.original_section.title)
        if target.original_section is not None and not scope.whole_document
        else frozenset()
    )
    disc_terms = [t for t in spec.terms if t not in orig_stems and t not in heading_stems]
    disc_phrases = [p for p in spec.phrases if p not in orig_norm]
    groups = [
        (label, [s for s in g if s not in orig_stems and s not in heading_stems])
        for g, label in zip(spec.concepts, spec.concept_labels, strict=False)
    ]
    groups = [(label, disc) for label, disc in groups if disc]
    growth = ev_section_growth(ctx, scope, target, CTX)
    tracked_ev = _tracked_evidence(ctx, scope, set(disc_terms) | {s for _, d in groups for s in d})

    # A brand-new section whose heading carries the requested terms.
    if re.search(r"\bsection\b", text, re.I) and disc_terms:
        for m in ctx.diff.sections:
            if m.kind.value == "added" and m.revised_id:
                sec = ctx.revised.section(m.revised_id)
                if (
                    sec
                    and set(disc_terms) <= set(stems_in(sec.title))
                    and ctx.revised.section_words(sec.id) >= 20
                ):
                    sec_ev = Evidence(
                        type=EvidenceType.SECTION_ADDED,
                        direction=S,
                        revised_location=ctx.section_loc(ctx.revised, sec),
                        explanation=f"new section '{sec.heading}' was added ({ctx.revised.section_words(sec.id)} words)",
                    )
                    return Assessment(
                        Status.RESOLVED,
                        RS.LEXICAL,
                        f"A new section '{sec.heading}' was added.",
                        "section-added",
                        [sec_ev],
                        quality=TargetQuality.SECTION,
                    )

    if not units:
        # Nothing new in the target scope. Was it added somewhere else instead?
        global_units = added_units(build_scope(ctx, Target()))
        elsewhere = [u for u in global_units if disc_terms and set(disc_terms) <= stems_in(u.text)]
        if elsewhere and target.located:
            ev = ev_added(
                ctx, elsewhere[:2], CTX, "this is outside the location the comment points to"
            )
            return Assessment(
                Status.NEEDS_REVIEW,
                RS.STRUCTURAL,
                "No new text was added where the comment points, but text containing the requested terms was added elsewhere.",
                "content-elsewhere",
                ev,
            )
        if target.section_removed:
            return Assessment(
                Status.NEEDS_REVIEW,
                RS.STRUCTURAL,
                "The section the comment refers to no longer exists in the revised document.",
                "section-removed",
                [
                    Evidence(
                        type=EvidenceType.SECTION_REMOVED,
                        direction=CTX,
                        original_location=ctx.section_loc(ctx.original, target.original_section),
                        explanation="the section has no counterpart in the revised document",
                    )
                ],
            )
        ev = _no_change_evidence(ctx, target, scope)
        if scope.changed:
            note = "the scope changed but no new text was added (only removals, edits or moves)"
        else:
            note = "the scope is unchanged"
        rationale = f"No new text was added in response to this request ({note})."
        if scope.whole_document:
            rationale = (
                "No new text matching the request was found anywhere in the revised document."
            )
        return Assessment(
            Status.UNRESOLVED,
            RS.DETERMINISTIC if not scope.changed else RS.STRUCTURAL,
            rationale,
            "no-new-text",
            ev,
            remaining=f"{_verb(category)} as requested.",
        )

    evidence_struct = ev_added(ctx, units, CTX)
    if growth is not None:
        evidence_struct.append(growth)
    evidence_struct.extend(tracked_ev)

    if subjective:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            f"New text was added ({added_words} words), but the request calls for a qualitative judgement "
            "(e.g. strength, clarity or rigour) that cannot be verified without semantic review.",
            "structural-only",
            evidence_struct,
        )

    checkable = bool(disc_terms or disc_phrases or groups)
    if not checkable:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            f"Structural evidence confirms new text was added ({added_words} words"
            + (
                f"; section {scope.words_before} → {scope.words_after} words"
                if growth is not None
                else ""
            )
            + "), but whether it adequately addresses the request requires semantic review.",
            "structural-only",
            evidence_struct,
        )

    def unit_stems(u_text: str) -> set[str]:
        return set(stems_in(u_text))

    if len(groups) >= 2:
        covered: list[str] = []
        missing: list[str] = []
        cover_units = []
        for label, disc in groups:
            hit = next((u for u in units if set(disc) <= unit_stems(u.text)), None)
            if hit:
                covered.append(label)
                cover_units.append(hit)
            else:
                missing.append(label)
        if covered and not missing and added_words >= MIN_WORDS.get(category, 8):
            return _lexical_resolved(
                ctx, cover_units, covered, evidence_struct, tracked_ev, scope, target
            )
        if covered and missing:
            ev = ev_added(ctx, cover_units, S, f"covers: {', '.join(covered)}") + tracked_ev
            ev.append(
                Evidence(
                    type=EvidenceType.NO_CHANGE,
                    direction=X,
                    explanation=f"no new text addresses: {', '.join(missing)}",
                )
            )
            return Assessment(
                Status.PARTIALLY_RESOLVED,
                RS.LEXICAL,
                f"New text addresses {', '.join(covered)} but not {', '.join(missing)}.",
                "concept-coverage",
                ev,
                missing=[f"no new text found for '{m}'" for m in missing],
                remaining=f"Add material addressing {', '.join(missing)}.",
            )
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            "New text was added, but none of it contains the specific concepts the comment names "
            f"({', '.join(label for label, _ in groups)}); it may use different wording, so this needs review.",
            "concept-coverage",
            evidence_struct,
        )

    need_terms = set(disc_terms)
    best = max(units, key=lambda u: len(need_terms & unit_stems(u.text)), default=None)
    phrase_ok = all(any(p in normalize(u.text) for u in units) for p in disc_phrases)
    if best is not None and need_terms <= unit_stems(best.text) and phrase_ok:
        growth_ok = True
        if category is C.EXPAND:
            before = max(scope.words_before, 1)
            growth_ok = (
                added_words >= MIN_WORDS[C.EXPAND] and added_words / before >= EXPAND_MIN_GROWTH
            )
        if added_words >= MIN_WORDS.get(category, 8) and growth_ok:
            hit_units = [u for u in units if need_terms <= unit_stems(u.text)] or [best]
            words = (
                _matched_words(" ".join(u.text for u in hit_units), need_terms)
                if need_terms
                else disc_phrases
            )
            return _lexical_resolved(
                ctx, hit_units, words, evidence_struct, tracked_ev, scope, target
            )
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            f"New text mentions the requested terms but is short ({added_words} words) for a request to {category.value.lower()}; "
            "whether it is sufficient needs review.",
            "insufficient-volume",
            evidence_struct,
        )
    found = sorted(need_terms & set().union(*(unit_stems(u.text) for u in units))) if units else []
    missing_terms = sorted(need_terms - set(found))
    shown = [words_of.get(t, t) for t in missing_terms]
    detail = (
        f"terms not found in the new text: {', '.join(shown)}"
        if shown
        else "the requested terms are spread across separate passages"
    )
    return Assessment(
        Status.NEEDS_REVIEW,
        RS.STRUCTURAL,
        f"Text was added ({added_words} words), but it does not clearly contain what was requested ({detail}). "
        "It may use different wording, so this needs review.",
        "lexical-partial",
        evidence_struct,
        missing=[f"'{w}' not found in the new text" for w in shown],
    )


def _lexical_resolved(
    ctx: AuditContext,
    units: list[AddedUnit],
    covered: list[str],
    struct_ev: list[Evidence],
    tracked_ev: list[Evidence],
    scope: Scope,
    target: Target,
) -> Assessment:
    ev = ev_added(ctx, units, S, f"contains the requested terms ({', '.join(covered)})")
    growth = ev_section_growth(ctx, scope, target, S)
    if growth is not None and growth.type is EvidenceType.TEXT_ADDED:
        ev.append(growth)
    for t in tracked_ev:
        t.direction = S
    ev.extend(tracked_ev)
    return Assessment(
        Status.RESOLVED,
        RS.LEXICAL,
        "New text containing the requested terms was added at the location the comment points to. "
        "This is lexical evidence: whether the text is accurate and sufficient has not been assessed.",
        "lexical-coverage",
        ev,
    )


def _tracked_evidence(ctx: AuditContext, scope: Scope, want: set[str]) -> list[Evidence]:
    out: list[Evidence] = []
    if not want:
        return out
    for t in ctx.revised.tracked_changes:
        if t.kind is not TrackedKind.INSERTION or t.story != "body":
            continue
        if not scope.whole_document and t.section_id not in scope.revised_ids:
            continue
        if want & stems_in(t.text):
            when = f" on {t.date[:10]}" if t.date else ""
            out.append(
                Evidence(
                    type=EvidenceType.TRACKED_REVISION,
                    direction=CTX,
                    revised_location=ctx.rev_loc(t.paragraph) if t.paragraph else None,
                    revised_text=truncate(t.text, 300),
                    explanation=f"tracked insertion by {t.author or 'unknown'}{when}",
                )
            )
    return out[:3]


def _verb(category: IssueCategory) -> str:
    return {
        C.ADD: "Add the requested content",
        C.EXPAND: "Expand the text",
        C.EXPLAIN: "Provide the explanation",
        C.CLARIFY: "Clarify the text",
    }.get(category, "Address the request")


# =================================================================================
# MOVE / STRUCTURE / GENERIC
# =================================================================================


def _move(spec: RequestSpec, target: Target, ctx: AuditContext) -> Assessment:
    if not target.original_paragraphs:
        return Assessment(
            Status.NOT_ASSESSABLE,
            RS.STRUCTURAL,
            "The comment asks for something to be moved but does not identify what.",
            "move-no-target",
        )
    ch = ctx.diff.change_for_original(target.original_paragraphs[0])
    if ch is None or ch.kind is ChangeKind.REMOVED:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            "The paragraph to move no longer exists in the revised document.",
            "move-check",
            [ev_change(ctx, ch, CTX)] if ch else [],
        )
    if ch.kind is ChangeKind.MOVED:
        rev_sec = ctx.revised.section_of(ch.revised) if ch.revised else None
        ev = ev_change(ctx, ch, S)
        if spec.destination_section and rev_sec is not None:
            if rev_sec.number == spec.destination_section:
                return Assessment(
                    Status.RESOLVED,
                    RS.DETERMINISTIC,
                    f"The paragraph now sits in Section {spec.destination_section}.",
                    "move-check",
                    [ev],
                )
            ev.direction = CTX
            return Assessment(
                Status.NEEDS_REVIEW,
                RS.STRUCTURAL,
                f"The paragraph was moved, but to '{rev_sec.heading}' rather than Section {spec.destination_section}.",
                "move-check",
                [ev],
            )
        ev.direction = CTX
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            "The paragraph was relocated; the comment does not name a destination that can be checked.",
            "move-check",
            [ev],
        )
    return Assessment(
        Status.UNRESOLVED,
        RS.DETERMINISTIC,
        "The paragraph is in the same place in the revised document.",
        "move-check",
        [ev_change(ctx, ch, X)],
        remaining="Move the paragraph as requested.",
    )


def _structural(target: Target, ctx: AuditContext, category: IssueCategory) -> Assessment:
    scope = build_scope(ctx, target)
    moves = sum(c.kind is ChangeKind.MOVED for c in scope.changes)
    adds = sum(c.kind is ChangeKind.ADDED for c in scope.changes)
    rems = sum(c.kind is ChangeKind.REMOVED for c in scope.changes)
    sec_changes = (
        ctx.diff.summary.sections_added
        + ctx.diff.summary.sections_removed
        + ctx.diff.summary.sections_renamed
    )
    if not scope.changed and not (scope.whole_document and sec_changes):
        return Assessment(
            Status.UNRESOLVED,
            RS.STRUCTURAL,
            "The relevant part of the document shows no structural change.",
            "structure-check",
            _no_change_evidence(ctx, target, scope),
            remaining=f"{category.value.title()} as requested.",
        )
    ev = [
        Evidence(
            type=EvidenceType.RELOCATION if moves else EvidenceType.TEXT_MODIFIED,
            direction=CTX,
            explanation=f"{adds} paragraph(s) added, {rems} removed, {moves} moved; "
            f"{ctx.diff.summary.sections_added} section(s) added, {ctx.diff.summary.sections_removed} removed",
        )
    ]
    return Assessment(
        Status.NEEDS_REVIEW,
        RS.STRUCTURAL,
        "Structural changes were made, but whether they achieve the requested reorganisation needs review.",
        "structure-check",
        ev,
    )


def _generic_change(
    text: str,
    spec: RequestSpec,
    target: Target,
    ctx: AuditContext,
    category: IssueCategory,
    subjective: bool = False,
) -> Assessment:
    if not target.located:
        return Assessment(
            Status.NOT_ASSESSABLE,
            RS.STRUCTURAL,
            "The comment cannot be tied to a place in the document (no usable anchor, quoted text or section), "
            "so there is no evidence to examine."
            + (" It also calls for a qualitative judgement." if subjective else ""),
            "no-target",
        )
    scope = build_scope(ctx, target)
    changes = changes_for_paragraphs(ctx, target.original_paragraphs)
    if target.table is not None:
        return _table(text, spec, target, ctx)
    if changes:
        changed = [c for c in changes if c.changed]
        if changed:
            ev = [ev_change(ctx, c, CTX) for c in changed[:3]]
            extra = ""
            if category is C.STATISTICAL:
                o_nums = {n for c in changed for n in numbers_in(c.original_text)}
                r_nums = {n for c in changed for n in numbers_in(c.revised_text)}
                if o_nums != r_nums:
                    extra = f" Numbers changed: {', '.join(sorted(o_nums - r_nums)) or 'none'} → {', '.join(sorted(r_nums - o_nums)) or 'none'}."
            return Assessment(
                Status.NEEDS_REVIEW,
                RS.STRUCTURAL,
                "The anchored text was changed, but the comment does not specify a checkable outcome, so whether "
                "the change is what the reviewer wanted needs review." + extra,
                "anchored-change",
                ev,
            )
        if scope.changed:
            return Assessment(
                Status.NEEDS_REVIEW,
                RS.STRUCTURAL,
                "The anchored text is unchanged, but the surrounding section was modified; the comment may have been addressed elsewhere.",
                "anchored-nearby-change",
                [
                    ev_change(ctx, changes[0], X, "anchored text unchanged"),
                    Evidence(
                        type=EvidenceType.CONTEXTUAL_MATCH,
                        direction=CTX,
                        explanation=f"{sum(c.changed for c in scope.changes)} paragraph(s) changed in the same section",
                    ),
                ],
            )
        return Assessment(
            Status.UNRESOLVED,
            RS.STRUCTURAL if subjective else RS.DETERMINISTIC,
            "The anchored text and its section are unchanged in the revised document.",
            "anchored-unchanged",
            [ev_change(ctx, changes[0], X, "the section is also unchanged")],
            remaining=f"{category.value.title()} the anchored text as requested.",
        )
    if scope.changed:
        return Assessment(
            Status.NEEDS_REVIEW,
            RS.STRUCTURAL,
            "The relevant section was modified; whether the modification addresses the request needs review.",
            "section-change",
            _section_change_evidence(ctx, scope, target),
        )
    return Assessment(
        Status.UNRESOLVED,
        RS.STRUCTURAL,
        "The relevant section is unchanged in the revised document.",
        "section-unchanged",
        _no_change_evidence(ctx, target, scope),
        remaining="Revise the section as requested.",
    )


def _section_change_evidence(ctx: AuditContext, scope: Scope, target: Target) -> list[Evidence]:
    ev = [ev_change(ctx, c, CTX) for c in [c for c in scope.changes if c.changed][:3]]
    growth = ev_section_growth(ctx, scope, target, CTX)
    if growth is not None:
        ev.append(growth)
    return ev


# =================================================================================
# dispatch, finalisation, aggregation
# =================================================================================


def _assess_part(
    issue: ReviewIssue, text: str, category: IssueCategory, target: Target, ctx: AuditContext
) -> Assessment:
    additive = category in (C.ADD, C.EXPAND, C.EXPLAIN, C.CLARIFY)
    spec = parse_request(text, want_concepts=additive)
    if not text.strip():
        return Assessment(
            Status.NOT_ASSESSABLE, RS.STRUCTURAL, "The comment is empty.", "empty-comment"
        )
    if not issue.actionable:
        return Assessment(
            Status.NOT_ASSESSABLE,
            RS.STRUCTURAL,
            "The comment does not ask for a change (it reads as an observation, question or acknowledgement).",
            "not-actionable",
        )
    if category is C.REMOVE:
        return _remove(text, spec, target, ctx)
    if category in (C.REPLACE, C.CORRECT):
        return _replace_or_correct(text, spec, target, ctx, category)
    if category is C.TABLE:
        return _table(text, spec, target, ctx)
    if category is C.FIGURE:
        return _figure(spec, target, ctx)
    if category in (C.REFERENCE, C.CITE):
        return _with_change(
            _reference(text, spec, target, ctx, category), ctx.diff.references.changed
        )
    if additive:
        if issue.subjective and not target.located and not spec.terms:
            return _generic_change(text, spec, target, ctx, category, subjective=True)
        return _additive(
            text, category, spec, target, ctx, issue.subjective and not (spec.terms or spec.phrases)
        )
    if category is C.MOVE:
        return _move(spec, target, ctx)
    if category in (C.MERGE, C.SPLIT, C.RESTRUCTURE):
        return _structural(target, ctx, category)
    if category is C.REFORMAT:
        style = f" ({spec.style_name.upper()})" if spec.style_name else ""
        return Assessment(
            Status.NOT_ASSESSABLE,
            RS.STRUCTURAL,
            f"Formatting and citation-style compliance{style} are not compared in this version of "
            "ReviewTrace; a dedicated style checker is needed.",
            "formatting",
        )
    if category is C.VERIFY:
        if target.located and target.original_paragraphs:
            a = _generic_change(text, spec, target, ctx, category)
            if a.status is Status.UNRESOLVED:
                a.status, a.method = Status.NEEDS_REVIEW, "verify"
                a.rationale = "The reviewer asked for something to be checked. The anchored text is unchanged, which does not show whether a check took place."
            return a
        return Assessment(
            Status.NOT_ASSESSABLE,
            RS.STRUCTURAL,
            "The comment asks for verification that ReviewTrace cannot perform from the two documents alone.",
            "verify",
        )
    if category is C.UPDATE and _mentions_references(text):
        return _with_change(
            _reference(text, spec, target, ctx, C.REFERENCE), ctx.diff.references.changed
        )
    return _generic_change(text, spec, target, ctx, category, subjective=issue.subjective)


def _with_change(a: Assessment, changed: bool) -> Assessment:
    a.changed = changed
    return a


def _mentions_references(text: str) -> bool:
    return bool(re.search(r"\b(references?|bibliograph\w*|citations?|literature)\b", text, re.I))


def finalize(issue: ReviewIssue, a: Assessment, target: Target, ctx: AuditContext) -> Finding:
    quality = a.quality if a.quality is not None else target.quality
    confidence = decide(a.strength, quality)
    status = a.status
    rationale = a.rationale
    if status is Status.RESOLVED and (
        confidence is Confidence.LOW or not any(e.direction is S for e in a.evidence)
    ):
        status = Status.NEEDS_REVIEW
        rationale += (
            " The evidence is not strong enough (the request could not be tied firmly to a location), "
            "so the status is NEEDS_REVIEW rather than RESOLVED."
        )
    if status in (Status.NEEDS_REVIEW, Status.NOT_ASSESSABLE) and confidence is Confidence.HIGH:
        confidence = Confidence.MEDIUM
    if status is Status.NOT_ASSESSABLE:
        confidence = (
            Confidence.LOW
            if a.strength is RS.STRUCTURAL and quality is TargetQuality.NONE
            else confidence
        )
    scope = build_scope(ctx, target) if target.located else None
    doc_changed = a.changed
    if doc_changed is None and target.table is not None:
        change = _find_table_change(ctx, target.table.index)
        doc_changed = change is None or change.kind is not TableChangeKind.UNCHANGED
    if doc_changed is None and scope is not None:
        doc_changed = scope.changed
    remaining = a.remaining
    if status in (Status.PARTIALLY_RESOLVED, Status.UNRESOLVED) and remaining is None:
        remaining = issue.requested_action or None
    return Finding(
        issue_id=issue.id,
        status=status,
        confidence=confidence,
        rationale=rationale,
        evidence=a.evidence,
        missing_elements=a.missing,
        remaining_action=remaining
        if status in (Status.PARTIALLY_RESOLVED, Status.UNRESOLVED)
        else None,
        requires_human_review=requires_human_review(status, confidence),
        method=a.method,
        located_by=target.how,
        document_changed=doc_changed,
        word_comment_resolved=issue.word_comment_resolved,
    )


def _aggregate(issue: ReviewIssue, subs: list[Finding]) -> Finding:
    statuses = [f.status for f in subs]
    supporting = [e for f in subs for e in f.evidence]
    missing: list[str] = []
    for sub_issue, f in zip(issue.sub_requirements, subs, strict=True):
        if f.status is not Status.RESOLVED:
            missing.append(
                f"{sub_issue.id}: {sub_issue.text.rstrip('.')} ({f.status.label.lower()})"
            )
        missing.extend(f"{sub_issue.id}: {m}" for m in f.missing_elements)

    resolvedish = [s for s in statuses if s in (Status.RESOLVED, Status.PARTIALLY_RESOLVED)]
    if Status.ERROR in statuses:
        status = Status.ERROR
    elif all(s is Status.RESOLVED for s in statuses):
        status = Status.RESOLVED
    elif all(s is Status.NOT_ASSESSABLE for s in statuses):
        status = Status.NOT_ASSESSABLE
    elif all(s is Status.UNRESOLVED for s in statuses):
        status = Status.UNRESOLVED
    elif resolvedish and any(s in (Status.UNRESOLVED, Status.PARTIALLY_RESOLVED) for s in statuses):
        status = Status.PARTIALLY_RESOLVED
    else:
        status = Status.NEEDS_REVIEW
    confidence = combine_confidence([f.confidence for f in subs])
    if status is Status.RESOLVED and confidence is Confidence.LOW:
        status = Status.NEEDS_REVIEW
    done = sum(s is Status.RESOLVED for s in statuses)
    rationale = (
        f"Compound comment with {len(subs)} requirements: {done} resolved. "
        + "; ".join(
            f"{si.id} {f.status.label.lower()}"
            for si, f in zip(issue.sub_requirements, subs, strict=True)
        )
        + "."
    )
    return Finding(
        issue_id=issue.id,
        status=status,
        confidence=confidence,
        rationale=rationale,
        evidence=supporting,
        missing_elements=missing if status is not Status.RESOLVED else [],
        remaining_action=("Address: " + "; ".join(missing[:4]))
        if status in (Status.PARTIALLY_RESOLVED, Status.UNRESOLVED) and missing
        else None,
        requires_human_review=requires_human_review(status, confidence),
        method="compound",
        located_by=subs[0].located_by if subs else "",
        document_changed=None
        if all(f.document_changed is None for f in subs)
        else any(bool(f.document_changed) for f in subs),
        word_comment_resolved=issue.word_comment_resolved,
        sub_findings=subs,
    )


def assess_issue(issue: ReviewIssue, ctx: AuditContext) -> Finding:
    """Assess one issue. Compound issues are assessed per requirement and then combined."""
    whole = parse_request(issue.comment_text, want_concepts=False)
    target = locate_target(issue, whole, ctx)
    if issue.is_compound:
        subs: list[Finding] = []
        for sub in issue.sub_requirements:
            part = issue.model_copy(
                update={
                    "comment_text": sub.text,
                    "category": sub.category,
                    "id": sub.id,
                    "sub_requirements": [],
                }
            )
            a = _assess_part(part, sub.text, sub.category, target, ctx)
            subs.append(finalize(part, a, target, ctx))
        return _aggregate(issue, subs)
    a = _assess_part(issue, issue.comment_text, issue.category, target, ctx)
    return finalize(issue, a, target, ctx)

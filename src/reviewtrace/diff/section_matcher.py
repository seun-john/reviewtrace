"""Match sections of the original document to sections of the revised document.

Headings are compared first (exact, then ignoring numbering), then by a blend of heading
similarity, body overlap and position, so that a renamed or renumbered section is still
recognised and an unrelated new section is not forced onto an old one.
"""

from __future__ import annotations

from collections.abc import Callable

from reviewtrace.models.diff import SectionMatch, SectionMatchKind
from reviewtrace.models.document import Document, Section
from reviewtrace.utils.text import char_similarity, jaccard, normalize, token_set, tokens

ACCEPT_HEADING = 0.8
ACCEPT_MIXED_CONTENT = 0.5
ACCEPT_MIXED_HEADING = 0.3
ACCEPT_CONTENT_ONLY = 0.75


def _mk(
    o: Section | None,
    r: Section | None,
    orig: Document,
    rev: Document,
    kind: SectionMatchKind,
    h: float = 0.0,
    c: float = 0.0,
) -> SectionMatch:
    return SectionMatch(
        kind=kind,
        original_id=o.id if o else None,
        revised_id=r.id if r else None,
        original_heading=o.heading if o else "",
        revised_heading=r.heading if r else "",
        heading_similarity=round(h, 3),
        content_similarity=round(c, 3),
        words_before=orig.section_words(o.id) if o else 0,
        words_after=rev.section_words(r.id) if r else 0,
    )


def _contains_words(short: list[str], long: list[str]) -> bool:
    n = len(short)
    return n > 0 and any(long[i : i + n] == short for i in range(len(long) - n + 1))


def _heading_similarity(a: str, b: str) -> float:
    """Character similarity, raised when one title is the other with words added around it
    ("Methods" -> "Materials and Methods")."""
    score = char_similarity(a, b)
    ta, tb = tokens(a), tokens(b)
    if ta and tb and ta != tb and (_contains_words(ta, tb) or _contains_words(tb, ta)):
        return max(score, ACCEPT_HEADING)
    return score


def match_sections(orig: Document, rev: Document) -> list[SectionMatch]:
    o_secs, r_secs = orig.sections, rev.sections
    # A heading with no body of its own (only subsections) is compared on its whole subtree.
    o_tokens = {s.id: token_set(orig.section_text(s.id, subtree=True)) for s in o_secs}
    r_tokens = {s.id: token_set(rev.section_text(s.id, subtree=True)) for s in r_secs}
    matched_o: dict[str, str] = {}
    matched_r: set[str] = set()
    results: dict[str, SectionMatch] = {}

    def commit(o: Section, r: Section, kind: SectionMatchKind, h: float, c: float) -> None:
        matched_o[o.id] = r.id
        matched_r.add(r.id)
        results[o.id] = _mk(o, r, orig, rev, kind, h, c)

    # Front matter always pairs with front matter.
    commit(
        o_secs[0], r_secs[0], SectionMatchKind.EXACT, 1.0, jaccard(o_tokens["S0"], r_tokens["S0"])
    )

    def unique_pass(key: Callable[[Section], str]) -> None:
        o_by: dict[str, list[Section]] = {}
        r_by: dict[str, list[Section]] = {}
        for s in o_secs[1:]:
            if s.id not in matched_o and key(s):
                o_by.setdefault(key(s), []).append(s)
        for s in r_secs[1:]:
            if s.id not in matched_r and key(s):
                r_by.setdefault(key(s), []).append(s)
        for k, os_ in o_by.items():
            rs_ = r_by.get(k, [])
            if len(os_) == 1 and len(rs_) == 1:
                commit(
                    os_[0],
                    rs_[0],
                    SectionMatchKind.EXACT,
                    1.0,
                    jaccard(o_tokens[os_[0].id], r_tokens[rs_[0].id]),
                )

    unique_pass(lambda s: normalize(s.heading))
    unique_pass(lambda s: normalize(s.title))

    no, nr = max(len(o_secs) - 1, 1), max(len(r_secs) - 1, 1)
    candidates: list[tuple[float, float, float, Section, Section]] = []
    for i, o in enumerate(o_secs[1:], start=0):
        if o.id in matched_o:
            continue
        for j, r in enumerate(r_secs[1:], start=0):
            if r.id in matched_r:
                continue
            h = _heading_similarity(o.title, r.title) if o.title and r.title else 0.0
            c = (
                jaccard(o_tokens[o.id], r_tokens[r.id])
                if (o_tokens[o.id] or r_tokens[r.id])
                else 0.0
            )
            accept = (
                h >= ACCEPT_HEADING
                or (c >= ACCEPT_MIXED_CONTENT and h >= ACCEPT_MIXED_HEADING)
                or c >= ACCEPT_CONTENT_ONLY
            )
            if not accept:
                continue
            pos = 1.0 - abs(i / no - j / nr)
            level = 1.0 if o.level == r.level else 0.0
            score = 0.55 * h + 0.30 * c + 0.10 * pos + 0.05 * level
            candidates.append((score, h, c, o, r))
    candidates.sort(key=lambda t: t[0], reverse=True)
    for _score, h, c, o, r in candidates:
        if o.id in matched_o or r.id in matched_r:
            continue
        if normalize(o.title) == normalize(r.title):
            kind = SectionMatchKind.EXACT
        elif h >= ACCEPT_HEADING:
            kind = SectionMatchKind.RENAMED
        else:
            kind = SectionMatchKind.CONTENT
        commit(o, r, kind, h, c)

    out: list[SectionMatch] = []
    for o in o_secs:
        out.append(results.get(o.id) or _mk(o, None, orig, rev, SectionMatchKind.REMOVED))
    for r in r_secs:
        if r.id not in matched_r:
            out.append(_mk(None, r, orig, rev, SectionMatchKind.ADDED))
    return out

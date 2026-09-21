"""Match paragraphs between two documents and classify each change.

Strategy, cheapest and most reliable first:

1. Paragraphs are aligned *inside* each pair of matched sections with a sequence diff on
   normalised text. Equal runs are unchanged; within a replaced run, the most similar
   pairs are matched as modified.
2. Whatever is left over, across the whole document, is matched by text similarity. A
   paragraph found in a different place is reported as moved rather than as an unrelated
   deletion plus insertion.
3. Anything still unmatched is removed (original) or added (revised).
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from reviewtrace.diff.text_diff import diff_words
from reviewtrace.extractors.docx import caption_info
from reviewtrace.models.diff import ChangeKind, ParagraphChange, SectionMatch
from reviewtrace.models.document import Document, Paragraph
from reviewtrace.utils.text import char_similarity, jaccard, normalize, similarity, token_set

MODIFIED_THRESHOLD = 0.5
MOVED_THRESHOLD = 0.6
MAX_BLOCK_PAIRS = 4000
MAX_GLOBAL_CANDIDATES = 4


@dataclass
class _Side:
    para: Paragraph
    norm: str
    tokens: frozenset[str]


def _side(p: Paragraph) -> _Side:
    return _Side(p, normalize(p.text), token_set(p.text))


def _change(
    kind: ChangeKind, o: Paragraph | None, r: Paragraph | None, sim: float
) -> ParagraphChange:
    added = removed = ""
    text_changed = False
    if o is not None and r is not None:
        text_changed = normalize(o.text) != normalize(r.text)
        if text_changed:
            wd = diff_words(o.text, r.text)
            added, removed = wd.added_text, wd.removed_text
    elif r is not None:
        added = r.text
    elif o is not None:
        removed = o.text
    ref = r or o
    return ParagraphChange(
        kind=kind,
        original=o.number if o else None,
        revised=r.number if r else None,
        similarity=round(sim, 3),
        original_text=o.text if o else "",
        revised_text=r.text if r else "",
        added_text=added,
        removed_text=removed,
        original_section_id=o.section_id if o else None,
        revised_section_id=r.section_id if r else None,
        is_heading=bool(ref and ref.is_heading),
        is_caption=bool(ref and not ref.is_heading and caption_info(ref.text)),
        text_changed=text_changed,
    )


def _greedy_pairs(
    left: list[_Side], right: list[_Side], threshold: float, exhaustive: bool
) -> list[tuple[_Side, _Side, float]]:
    scored: list[tuple[float, int, int]] = []
    for i, a in enumerate(left):
        cands: list[tuple[float, int]] = []
        for j, b in enumerate(right):
            if not exhaustive and jaccard(a.tokens, b.tokens) < 0.25:
                continue
            la, lb = len(a.norm), len(b.norm)
            if la and lb and min(la, lb) / max(la, lb) < 0.15:
                continue
            cands.append((similarity(a.para.text, b.para.text), j))
        cands.sort(reverse=True)
        for s, j in cands[: MAX_GLOBAL_CANDIDATES if not exhaustive else len(cands)]:
            if s >= threshold:
                scored.append((s, i, j))
    scored.sort(reverse=True)
    used_l: set[int] = set()
    used_r: set[int] = set()
    out: list[tuple[_Side, _Side, float]] = []
    for s, i, j in scored:
        if i in used_l or j in used_r:
            continue
        used_l.add(i)
        used_r.add(j)
        out.append((left[i], right[j], s))
    return out


def match_paragraphs(
    orig: Document, rev: Document, sections: list[SectionMatch]
) -> list[ParagraphChange]:
    changes: list[ParagraphChange] = []
    left_over_o: list[_Side] = []
    left_over_r: list[_Side] = []
    matched_o: set[int] = set()
    matched_r: set[int] = set()

    for m in sections:
        if m.original_id and m.revised_id:
            o_paras = list(orig.section_paragraphs(m.original_id))
            r_paras = list(rev.section_paragraphs(m.revised_id))
            o_head = next((p for p in o_paras if p.is_heading), None)
            r_head = next((p for p in r_paras if p.is_heading), None)
            if o_head and r_head:
                same = normalize(o_head.text) == normalize(r_head.text)
                sim = 1.0 if same else char_similarity(o_head.text, r_head.text)
                changes.append(
                    _change(
                        ChangeKind.UNCHANGED if same else ChangeKind.MODIFIED, o_head, r_head, sim
                    )
                )
                matched_o.add(o_head.number)
                matched_r.add(r_head.number)
            o_body = [_side(p) for p in o_paras if not p.is_heading]
            r_body = [_side(p) for p in r_paras if not p.is_heading]
            sm = SequenceMatcher(
                None, [s.norm for s in o_body], [s.norm for s in r_body], autojunk=False
            )
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "equal":
                    for a, b in zip(o_body[i1:i2], r_body[j1:j2], strict=True):
                        changes.append(_change(ChangeKind.UNCHANGED, a.para, b.para, 1.0))
                        matched_o.add(a.para.number)
                        matched_r.add(b.para.number)
                    continue
                block_o, block_r = o_body[i1:i2], r_body[j1:j2]
                paired_o: set[int] = set()
                paired_r: set[int] = set()
                if block_o and block_r:
                    exhaustive = len(block_o) * len(block_r) <= MAX_BLOCK_PAIRS
                    for a, b, s in _greedy_pairs(block_o, block_r, MODIFIED_THRESHOLD, exhaustive):
                        changes.append(_change(ChangeKind.MODIFIED, a.para, b.para, s))
                        matched_o.add(a.para.number)
                        matched_r.add(b.para.number)
                        paired_o.add(a.para.number)
                        paired_r.add(b.para.number)
                left_over_o.extend(a for a in block_o if a.para.number not in paired_o)
                left_over_r.extend(b for b in block_r if b.para.number not in paired_r)
        elif m.original_id:
            left_over_o.extend(_side(p) for p in orig.section_paragraphs(m.original_id))
        elif m.revised_id:
            left_over_r.extend(_side(p) for p in rev.section_paragraphs(m.revised_id))

    # Global pass: same or similar text somewhere else -> moved.
    pool_o = [s for s in left_over_o if s.para.number not in matched_o]
    pool_r = [s for s in left_over_r if s.para.number not in matched_r]
    by_norm: dict[str, list[_Side]] = {}
    for side in pool_r:
        by_norm.setdefault(side.norm, []).append(side)
    still_o: list[_Side] = []
    for a in pool_o:
        bucket = by_norm.get(a.norm)
        if bucket and a.norm:
            b = bucket.pop(0)
            changes.append(_change(ChangeKind.MOVED, a.para, b.para, 1.0))
            matched_o.add(a.para.number)
            matched_r.add(b.para.number)
        else:
            still_o.append(a)
    still_r = [side for side in pool_r if side.para.number not in matched_r]
    for a, b, s in _greedy_pairs(still_o, still_r, MOVED_THRESHOLD, exhaustive=False):
        changes.append(_change(ChangeKind.MOVED, a.para, b.para, s))
        matched_o.add(a.para.number)
        matched_r.add(b.para.number)

    for p in orig.paragraphs:
        if p.number not in matched_o:
            changes.append(_change(ChangeKind.REMOVED, p, None, 0.0))
    for p in rev.paragraphs:
        if p.number not in matched_r:
            changes.append(_change(ChangeKind.ADDED, None, p, 0.0))

    return _ordered(changes)


def _ordered(changes: list[ParagraphChange]) -> list[ParagraphChange]:
    """Order by revised position; removed paragraphs sit after their nearest predecessor."""
    anchor: dict[int, int] = {c.original: c.revised for c in changes if c.original and c.revised}
    keyed: list[tuple[float, int, ParagraphChange]] = []
    for c in changes:
        if c.revised is not None:
            key = float(c.revised)
        else:
            prev = [rv for og, rv in anchor.items() if c.original is not None and og < c.original]
            key = (max(prev) if prev else 0) + 0.5
        keyed.append((key, c.original or 0, c))
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in keyed]

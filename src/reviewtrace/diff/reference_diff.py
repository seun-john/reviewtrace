"""Reference-list change detection: added, removed, modified entries and publication years."""

from __future__ import annotations

from reviewtrace.models.diff import ReferenceChange
from reviewtrace.models.document import Document
from reviewtrace.utils.text import normalize, similarity

MODIFIED_LOW = 0.6


def diff_references(orig: Document, rev: Document) -> ReferenceChange:
    o_refs, r_refs = list(orig.references), list(rev.references)
    change = ReferenceChange(
        original_count=len(o_refs),
        revised_count=len(r_refs),
        original_years=[r.year for r in o_refs if r.year],
        revised_years=[r.year for r in r_refs if r.year],
    )
    o_left = dict(enumerate(o_refs))
    r_left = dict(enumerate(r_refs))

    o_norm = {i: normalize(r.raw) for i, r in o_left.items()}
    for ri, rr in list(r_left.items()):
        rn = normalize(rr.raw)
        hit = next((oi for oi, on in o_norm.items() if oi in o_left and on == rn), None)
        if hit is not None:
            del o_left[hit]
            del r_left[ri]

    pairs: list[tuple[float, int, int]] = []
    for oi, orr in o_left.items():
        for ri, rr in r_left.items():
            s = similarity(orr.raw, rr.raw)
            if s >= MODIFIED_LOW:
                pairs.append((s, oi, ri))
    pairs.sort(reverse=True)
    for _s, oi, ri in pairs:
        if oi in o_left and ri in r_left:
            change.modified.append((o_left[oi].raw, r_left[ri].raw))
            del o_left[oi]
            del r_left[ri]

    change.removed = [r.raw for r in o_left.values()]
    change.added = [r.raw for r in r_left.values()]
    change.added_years = [r.year for r in r_left.values()]
    return change

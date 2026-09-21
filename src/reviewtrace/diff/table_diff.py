"""Basic table comparison: added/removed tables, rows, columns, cell edits and captions."""

from __future__ import annotations

from reviewtrace.models.diff import CellChange, TableChange, TableChangeKind
from reviewtrace.models.document import Document, Table
from reviewtrace.utils.text import char_similarity, jaccard, normalize, token_set

CAPTION_MATCH = 0.8
CONTENT_MATCH = 0.5


def _cells(table: Table) -> frozenset[str]:
    return token_set(" ".join(" ".join(row) for row in table.rows[:3]))


def _pair_tables(
    orig: Document, rev: Document
) -> tuple[list[tuple[Table, Table]], list[Table], list[Table]]:
    o_left, r_left = list(orig.tables), list(rev.tables)
    pairs: list[tuple[Table, Table]] = []

    # 1) identical labels ("Table 4.2") when unique on both sides
    for o in list(o_left):
        if not o.label:
            continue
        o_same = [t for t in o_left if t.label == o.label]
        r_same = [t for t in r_left if t.label == o.label]
        if len(o_same) == 1 and len(r_same) == 1:
            pairs.append((o, r_same[0]))
            o_left.remove(o)
            r_left.remove(r_same[0])

    # 2) best remaining by caption + header/first-rows content
    scored: list[tuple[float, Table, Table]] = []
    for o in o_left:
        for r in r_left:
            cap = char_similarity(o.caption, r.caption) if o.caption and r.caption else 0.0
            content = jaccard(_cells(o), _cells(r))
            if cap >= CAPTION_MATCH or content >= CONTENT_MATCH:
                scored.append(
                    (max(cap, content) + (0.1 if o.section_id == r.section_id else 0.0), o, r)
                )
    scored.sort(key=lambda t: t[0], reverse=True)
    for _s, o, r in scored:
        if o in o_left and r in r_left:
            pairs.append((o, r))
            o_left.remove(o)
            r_left.remove(r)
    return pairs, o_left, r_left


def _row_key(row: list[str]) -> str:
    return normalize(row[0]) if row else ""


def _compare(o: Table, r: Table) -> TableChange:
    change = TableChange(
        kind=TableChangeKind.UNCHANGED,
        original_index=o.index,
        revised_index=r.index,
        original_caption=o.caption,
        revised_caption=r.caption,
    )
    if normalize(o.caption or "") != normalize(r.caption or ""):
        change.caption_changed = True

    o_head, r_head = o.header, r.header
    o_cols = {normalize(h): i for i, h in enumerate(o_head) if normalize(h)}
    r_cols = {normalize(h): i for i, h in enumerate(r_head) if normalize(h)}
    col_map: dict[int, int] = {}
    if o_cols and r_cols and len(o_cols) >= max(1, len(o_head) - 1):
        for name, oi in o_cols.items():
            if name in r_cols:
                col_map[oi] = r_cols[name]
        change.columns_removed = [h for h in o_head if normalize(h) and normalize(h) not in r_cols]
        change.columns_added = [h for h in r_head if normalize(h) and normalize(h) not in o_cols]
    else:
        width = min(len(o_head), len(r_head))
        col_map = {i: i for i in range(width)}
        if len(o_head) > len(r_head):
            change.columns_removed = [f"column {i + 1}" for i in range(len(r_head), len(o_head))]
        elif len(r_head) > len(o_head):
            change.columns_added = [f"column {i + 1}" for i in range(len(o_head), len(r_head))]

    o_rows, r_rows = o.rows[1:] if len(o.rows) > 1 else [], r.rows[1:] if len(r.rows) > 1 else []
    o_keys = [_row_key(x) for x in o_rows]
    r_keys = [_row_key(x) for x in r_rows]
    keyed = (
        len(set(o_keys)) == len(o_keys)
        and len(set(r_keys)) == len(r_keys)
        and all(o_keys)
        and all(r_keys)
    )
    row_pairs: list[tuple[int, int]] = []
    if keyed:
        r_index = {k: i for i, k in enumerate(r_keys)}
        for i, k in enumerate(o_keys):
            if k in r_index:
                row_pairs.append((i, r_index[k]))
        change.rows_removed = [o_rows[i][0] for i, k in enumerate(o_keys) if k not in r_index]
        o_index = set(o_keys)
        change.rows_added = [r_rows[i][0] for i, k in enumerate(r_keys) if k not in o_index]
    else:
        common = min(len(o_rows), len(r_rows))
        row_pairs = [(i, i) for i in range(common)]
        change.rows_removed = [f"row {i + 2}" for i in range(common, len(o_rows))]
        change.rows_added = [f"row {i + 2}" for i in range(common, len(r_rows))]

    # Header cells, then body cells, for every paired (row, column).
    header_pairs = [(-1, -1), *row_pairs]
    for oi, ri in header_pairs:
        orow = o_head if oi == -1 else o_rows[oi]
        rrow = r_head if ri == -1 else r_rows[ri]
        label = (_row_key(orow) and orow[0]) or f"row {oi + 2}"
        for oc, rc in col_map.items():
            ov = orow[oc] if oc < len(orow) else ""
            rv = rrow[rc] if rc < len(rrow) else ""
            if normalize(ov) != normalize(rv):
                change.cell_changes.append(
                    CellChange(
                        row=oi + 1,
                        col=oc,
                        row_label=label,
                        col_label=o_head[oc] if oc < len(o_head) else f"column {oc + 1}",
                        original=ov,
                        revised=rv,
                    )
                )

    if (
        change.caption_changed
        or change.rows_added
        or change.rows_removed
        or change.columns_added
        or change.columns_removed
        or change.cell_changes
    ):
        change.kind = TableChangeKind.MODIFIED
    return change


def diff_tables(orig: Document, rev: Document) -> list[TableChange]:
    pairs, removed, added = _pair_tables(orig, rev)
    out = [_compare(o, r) for o, r in pairs]
    out.extend(
        TableChange(
            kind=TableChangeKind.REMOVED, original_index=t.index, original_caption=t.caption
        )
        for t in removed
    )
    out.extend(
        TableChange(kind=TableChangeKind.ADDED, revised_index=t.index, revised_caption=t.caption)
        for t in added
    )
    out.sort(
        key=lambda c: (
            c.revised_index if c.revised_index is not None else 10_000 + (c.original_index or 0)
        )
    )
    return out

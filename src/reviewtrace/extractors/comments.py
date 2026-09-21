"""Turn raw Word comments into :class:`ReviewComment` objects with anchors and context.

A comment such as "Explain this." is meaningless without the text it is attached to, so
the anchored text, its paragraph, its section and the neighbouring paragraphs are all
captured here.
"""

from __future__ import annotations

from reviewtrace.extractors.ooxml import RangeBuf, RawComment, RawParagraph, Story
from reviewtrace.models.document import CommentReply, Document, Location, ReviewComment

MAX_COMMENT_CHARS = 20_000
CONTEXT_PARAGRAPHS = 2


def _range_paragraphs(buf: RangeBuf) -> tuple[list[str], list[RawParagraph]]:
    texts: list[str] = []
    pars: list[RawParagraph] = []
    for rp, chunk in buf.chunks:
        if not pars or pars[-1] is not rp:
            pars.append(rp)
            texts.append(chunk)
        else:
            texts[-1] += chunk
    return [t.strip() for t in texts if t.strip()], pars


def _locate(
    pars: list[RawParagraph], doc: Document, story_name: str
) -> tuple[Location, str, list[str], list[str]]:
    """Location, anchor paragraph text, and context paragraphs for a range."""
    if not pars:
        return Location(), "", [], []
    first, last = pars[0], pars[-1]
    if first.story != "body":
        return Location(note=f"in {first.story}"), first.text.strip(), [], []
    if first.table is not None:
        idx = first.table[0]
        table = doc.tables[idx] if idx < len(doc.tables) else None
        sec = doc.section(table.section_id) if table else doc.section("S0")
        loc = Location(
            section_id=sec.id if sec else None,
            section=sec.heading if sec and sec.heading else None,
            table_index=idx,
            cell=(first.table[1], first.table[2]),
        )
        return loc, first.text.strip(), [], []
    start = first.number or first.prev_number
    end = last.number or last.prev_number if last.table is None else start
    if not start:
        sec0 = doc.section("S0")
        return Location(section_id=sec0.id if sec0 else None), first.text.strip(), [], []
    loc = doc.location_of(start, end if end and end != start else None)
    before = [p.text for p in doc.paragraphs if start - CONTEXT_PARAGRAPHS <= p.number < start]
    after_from = (end or start) + 1
    after = [
        p.text for p in doc.paragraphs if after_from <= p.number < after_from + CONTEXT_PARAGRAPHS
    ]
    para = doc.paragraph(start)
    return loc, para.text if para else first.text.strip(), before, after


def build_comments(
    raw: list[RawComment],
    body: Story,
    notes: list[tuple[str, Story]],
    doc: Document,
) -> list[ReviewComment]:
    ranges: dict[str, RangeBuf] = dict(body.ranges)
    references: dict[str, RawParagraph] = dict(body.references)
    for _nid, st in notes:
        for cid, note_buf in st.ranges.items():
            ranges.setdefault(cid, note_buf)
        for cid, rp in st.references.items():
            references.setdefault(cid, rp)

    para_to_comment: dict[str, str] = {}
    for c in raw:
        for pid in c.para_ids:
            para_to_comment[pid] = c.id
    parent_of: dict[str, str] = {}
    for c in raw:
        if c.parent_para_id and c.parent_para_id in para_to_comment:
            parent_of[c.id] = para_to_comment[c.parent_para_id]

    def root_of(cid: str) -> str:
        seen = {cid}
        while cid in parent_of and parent_of[cid] not in seen:
            cid = parent_of[cid]
            seen.add(cid)
        return cid

    built: dict[str, ReviewComment] = {}
    for c in raw:
        text = "\n".join(p for p in c.paragraphs if p.strip()).strip()
        truncated = len(text) > MAX_COMMENT_CHARS
        if truncated:
            text = text[:MAX_COMMENT_CHARS] + "…"
        buf = ranges.get(c.id)
        anchor_text = ""
        pars: list[RawParagraph] = []
        if buf is not None:
            texts, pars = _range_paragraphs(buf)
            anchor_text = "\n".join(texts)
            if not pars and buf.start_par is not None:
                pars = [buf.start_par]
        elif c.id in references:
            pars = [references[c.id]]
        loc, para_text, before, after = _locate(pars, doc, "body")
        built[c.id] = ReviewComment(
            id=c.id,
            author=c.author,
            initials=c.initials,
            date=c.date,
            text=text,
            paragraphs=[p for p in c.paragraphs if p.strip()],
            anchor_text=anchor_text[:MAX_COMMENT_CHARS],
            anchor=loc,
            context_before=before,
            context_after=after,
            anchor_paragraph_text=para_text,
            parent_id=parent_of.get(c.id),
            word_comment_resolved=c.done,
            truncated=truncated,
        )

    for c in raw:
        if c.id in parent_of:
            root = root_of(c.id)
            if root in built and root != c.id:
                mine = built[c.id]
                built[root].replies.append(
                    CommentReply(id=mine.id, author=mine.author, date=mine.date, text=mine.text)
                )
    return [built[c.id] for c in raw]

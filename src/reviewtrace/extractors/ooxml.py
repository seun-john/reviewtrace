"""Read-only OOXML access for review metadata that python-docx does not expose.

python-docx has no public API for comment threads (``commentsExtended.xml``), comment
ranges, tracked revisions or footnote stories, so this module reads the package
directly. It never writes to the source file.

Hardening: the ZIP is inspected before anything is decompressed (entry count, declared
sizes) and every part read is size-capped; XML is parsed with entity resolution, DTD
loading and network access disabled.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from reviewtrace.utils.errors import DocumentReadError

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
W16CID = "http://schemas.microsoft.com/office/word/2016/wordml/cid"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"

MAX_ENTRIES = 20_000
MAX_PART_BYTES = 150 * 1024 * 1024
MAX_TOTAL_BYTES = 600 * 1024 * 1024

_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
    remove_blank_text=False,
)

Element = etree._Element


def q(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


def local(el: Element) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def wattr(el: Element, name: str) -> str | None:
    return el.get(q(W, name))


class OoxmlPackage:
    """A DOCX ZIP package opened for reading only."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self._zip = zipfile.ZipFile(path, "r")
        except (zipfile.BadZipFile, OSError) as exc:
            raise DocumentReadError(f"{path.name}: not a valid DOCX (ZIP) file ({exc})") from exc
        self._check_limits()
        self._names = set(self._zip.namelist())

    def _check_limits(self) -> None:
        infos = self._zip.infolist()
        if len(infos) > MAX_ENTRIES:
            raise DocumentReadError(f"{self.path.name}: too many ZIP entries ({len(infos)})")
        total = sum(i.file_size for i in infos)
        if total > MAX_TOTAL_BYTES or any(i.file_size > MAX_PART_BYTES for i in infos):
            raise DocumentReadError(f"{self.path.name}: package expands to an unsafe size")

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> OoxmlPackage:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def read_part(self, name: str) -> bytes | None:
        if name not in self._names:
            return None
        try:
            with self._zip.open(name) as fh:
                data = fh.read(MAX_PART_BYTES + 1)
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            raise DocumentReadError(f"{self.path.name}: cannot read {name} ({exc})") from exc
        if len(data) > MAX_PART_BYTES:
            raise DocumentReadError(f"{self.path.name}: {name} is unreasonably large")
        return data

    def parse_part(self, name: str) -> Element | None:
        data = self.read_part(name)
        if data is None:
            return None
        try:
            return etree.fromstring(data, parser=_PARSER)
        except etree.XMLSyntaxError as exc:
            raise DocumentReadError(f"{self.path.name}: malformed XML in {name} ({exc})") from exc


# ---------------------------------------------------------------------------------
# Story walking (document body, footnotes, endnotes)
# ---------------------------------------------------------------------------------


@dataclass
class RawParagraph:
    element: Element
    story: str
    table: tuple[int, int, int] | None = None
    parts: list[str] = field(default_factory=list)
    is_list: bool = False
    has_image: bool = False
    style_id: str | None = None
    outline_level: int | None = None
    number: int = 0
    prev_number: int = 0

    @property
    def text(self) -> str:
        return "".join(self.parts)


@dataclass
class RawTracked:
    kind: str  # "insertion" | "deletion"
    id: str
    author: str
    date: str | None
    moved: bool
    paragraph: RawParagraph
    text: str = ""


@dataclass
class RawTable:
    index: int
    rows: list[list[str]]
    after: RawParagraph | None


@dataclass
class RangeBuf:
    chunks: list[tuple[RawParagraph, str]] = field(default_factory=list)
    start_par: RawParagraph | None = None
    closed: bool = False


@dataclass
class Story:
    name: str
    paragraphs: list[RawParagraph] = field(default_factory=list)
    tables: list[RawTable] = field(default_factory=list)
    tracked: list[RawTracked] = field(default_factory=list)
    ranges: dict[str, RangeBuf] = field(default_factory=dict)
    references: dict[str, RawParagraph] = field(default_factory=dict)


_INLINE_WRAPPERS = {
    "hyperlink",
    "smartTag",
    "fldSimple",
    "customXml",
    "bdo",
    "dir",
    "sdt",
    "sdtContent",
}
_BLOCK_WRAPPERS = {"customXml", "ins", "del", "moveTo", "moveFrom", "sdtContent"}


class _Walker:
    def __init__(self, name: str) -> None:
        self.story = Story(name=name)
        self._open: set[str] = set()
        self._last_body: RawParagraph | None = None
        self._cell: tuple[int, int, int] | None = None

    # -- blocks ------------------------------------------------------------------
    def blocks(self, parent: Element, cell: tuple[int, int, int] | None = None) -> None:
        for child in parent:
            tag = local(child)
            if tag == "p":
                self._paragraph(child, cell)
            elif tag == "tbl":
                self._table(child, cell)
            elif tag == "sdt":
                content = child.find(q(W, "sdtContent"))
                if content is not None:
                    self.blocks(content, cell)
            elif tag in _BLOCK_WRAPPERS:
                self.blocks(child, cell)
            elif tag == "commentRangeStart":
                self._range_start(child, None)
            elif tag == "commentRangeEnd":
                self._range_end(child)
            elif tag == "AlternateContent":
                choice = child.find(q(MC, "Choice"))
                if choice is not None:
                    self.blocks(choice, cell)

    def _table(self, tbl: Element, outer_cell: tuple[int, int, int] | None) -> None:
        if outer_cell is not None:
            # Nested table: fold its paragraphs into the enclosing cell.
            for row in tbl.iterfind(q(W, "tr")):
                for tc in row.iterfind(q(W, "tc")):
                    self.blocks(tc, outer_cell)
            return
        index = len(self.story.tables)
        rows: list[list[str]] = []
        raw = RawTable(index=index, rows=rows, after=self._last_body)
        self.story.tables.append(raw)
        for r, tr in enumerate(tbl.iterfind(q(W, "tr"))):
            row_cells: list[str] = []
            for c, tc in enumerate(tr.iterfind(q(W, "tc"))):
                before = len(self.story.paragraphs)
                self.blocks(tc, (index, r, c))
                cell_text = "\n".join(
                    p.text.strip() for p in self.story.paragraphs[before:] if p.text.strip()
                )
                row_cells.append(cell_text)
            rows.append(row_cells)

    def _paragraph(self, p: Element, cell: tuple[int, int, int] | None) -> None:
        rp = RawParagraph(element=p, story=self.story.name, table=cell)
        ppr = p.find(q(W, "pPr"))
        if ppr is not None:
            style = ppr.find(q(W, "pStyle"))
            if style is not None:
                rp.style_id = wattr(style, "val")
            outline = ppr.find(q(W, "outlineLvl"))
            if outline is not None:
                val = wattr(outline, "val")
                if val is not None and val.isdigit():
                    rp.outline_level = int(val)
            if ppr.find(q(W, "numPr")) is not None:
                rp.is_list = True
        self.story.paragraphs.append(rp)
        if cell is None:
            self._last_body = rp
        self._inline(p, rp, deleted=False, change=None)

    # -- inline ------------------------------------------------------------------
    def _inline(
        self, parent: Element, rp: RawParagraph, deleted: bool, change: RawTracked | None
    ) -> None:
        for child in parent:
            tag = local(child)
            if tag == "r":
                self._run(child, rp, deleted, change)
            elif tag in ("ins", "moveTo"):
                self._tracked(child, rp, "insertion", tag == "moveTo", deleted)
            elif tag in ("del", "moveFrom"):
                self._tracked(child, rp, "deletion", tag == "moveFrom", deleted)
            elif tag == "commentRangeStart":
                self._range_start(child, rp)
            elif tag == "commentRangeEnd":
                self._range_end(child)
            elif tag in _INLINE_WRAPPERS:
                target = child.find(q(W, "sdtContent")) if tag == "sdt" else child
                if target is not None:
                    self._inline(target, rp, deleted, change)
            elif tag == "AlternateContent":
                choice = child.find(q(MC, "Choice"))
                if choice is not None:
                    self._inline(choice, rp, deleted, change)

    def _tracked(
        self, el: Element, rp: RawParagraph, kind: str, moved: bool, deleted: bool
    ) -> None:
        raw = RawTracked(
            kind=kind,
            id=wattr(el, "id") or "",
            author=wattr(el, "author") or "",
            date=wattr(el, "date"),
            moved=moved,
            paragraph=rp,
        )
        self._inline(el, rp, deleted=(kind == "deletion") or deleted, change=raw)
        if raw.text.strip():
            self.story.tracked.append(raw)

    def _run(self, r: Element, rp: RawParagraph, deleted: bool, change: RawTracked | None) -> None:
        for child in r:
            tag = local(child)
            text: str | None = None
            if tag in ("t", "delText"):
                text = child.text or ""
            elif tag == "tab":
                text = "\t"
            elif tag in ("br", "cr"):
                text = " " if wattr(child, "type") in (None, "textWrapping") else ""
            elif tag == "noBreakHyphen":
                text = "-"
            elif tag == "commentReference":
                cid = wattr(child, "id")
                if cid is not None:
                    self.story.references.setdefault(cid, rp)
            elif tag in ("drawing", "pict", "object"):
                rp.has_image = True
            if text is None or text == "":
                continue
            if deleted:
                if change is not None:
                    change.text += text
                continue
            rp.parts.append(text)
            if change is not None:
                change.text += text
            for cid in self._open:
                buf = self.story.ranges.get(cid)
                if buf is not None:
                    buf.chunks.append((rp, text))

    # -- comment ranges ----------------------------------------------------------
    def _range_start(self, el: Element, rp: RawParagraph | None) -> None:
        cid = wattr(el, "id")
        if cid is None:
            return
        buf = self.story.ranges.setdefault(cid, RangeBuf())
        buf.start_par = rp if rp is not None else self._last_body
        self._open.add(cid)

    def _range_end(self, el: Element) -> None:
        cid = wattr(el, "id")
        if cid is None:
            return
        self._open.discard(cid)
        buf = self.story.ranges.get(cid)
        if buf is not None:
            buf.closed = True


def _finalize(story: Story) -> None:
    n = 0
    prev = 0
    for rp in story.paragraphs:
        if rp.table is None and rp.text.strip():
            n += 1
            rp.number = n
        if rp.number:
            prev = rp.number
        rp.prev_number = prev


def walk_story(root: Element, name: str = "body") -> Story:
    """Walk a body/footnote/endnote element in document order."""
    walker = _Walker(name)
    walker.blocks(root)
    _finalize(walker.story)
    return walker.story


# ---------------------------------------------------------------------------------
# Comments parts
# ---------------------------------------------------------------------------------


@dataclass
class RawComment:
    id: str
    author: str
    initials: str
    date: str | None
    paragraphs: list[str]
    para_ids: list[str]
    durable_id: str | None = None
    parent_para_id: str | None = None
    done: bool | None = None


def _plain_text(el: Element) -> str:
    out: list[str] = []
    for node in el.iter():
        tag = local(node)
        if tag == "t":
            out.append(node.text or "")
        elif tag == "tab":
            out.append("\t")
        elif tag in ("br", "cr"):
            out.append(" ")
    return "".join(out)


def parse_comments(pkg: OoxmlPackage) -> list[RawComment]:
    """Parse ``comments.xml`` plus the thread/resolved metadata in the extension parts."""
    root = pkg.parse_part("word/comments.xml")
    if root is None:
        return []
    extended: dict[str, tuple[str | None, bool | None]] = {}
    ext_root = _optional_part(pkg, "word/commentsExtended.xml")
    if ext_root is not None:
        for ex in ext_root.iter(q(W15, "commentEx")):
            pid = ex.get(q(W15, "paraId"))
            if pid:
                done = ex.get(q(W15, "done"))
                extended[pid] = (
                    ex.get(q(W15, "paraIdParent")),
                    None if done is None else done in ("1", "true"),
                )
    durable: dict[str, str] = {}
    ids_root = _optional_part(pkg, "word/commentsIds.xml")
    if ids_root is not None:
        for ci in ids_root.iter(q(W16CID, "commentId")):
            pid = ci.get(q(W16CID, "paraId"))
            did = ci.get(q(W16CID, "durableId"))
            if pid and did:
                durable[pid] = did

    comments: list[RawComment] = []
    for c in root.iter(q(W, "comment")):
        cid = wattr(c, "id")
        if cid is None:
            continue
        paragraphs: list[str] = []
        para_ids: list[str] = []
        for p in c.iter(q(W, "p")):
            paragraphs.append(_plain_text(p))
            pid = p.get(q(W14, "paraId"))
            if pid:
                para_ids.append(pid)
        rc = RawComment(
            id=cid,
            author=wattr(c, "author") or "",
            initials=wattr(c, "initials") or "",
            date=wattr(c, "date"),
            paragraphs=paragraphs,
            para_ids=para_ids,
        )
        for pid in reversed(para_ids):  # Word keys thread metadata on the last paragraph
            if pid in extended:
                rc.parent_para_id, rc.done = extended[pid]
                break
        for pid in para_ids:
            if pid in durable:
                rc.durable_id = durable[pid]
                break
        comments.append(rc)
    return comments


def _optional_part(pkg: OoxmlPackage, name: str) -> Element | None:
    """Extension parts are best-effort: a malformed one must not sink the extraction."""
    try:
        return pkg.parse_part(name)
    except DocumentReadError:
        return None


def parse_notes(pkg: OoxmlPackage, part: str, kind: str) -> list[tuple[str, Story]]:
    """Walk each real footnote/endnote as its own story."""
    root = _optional_part(pkg, part)
    if root is None:
        return []
    out: list[tuple[str, Story]] = []
    for note in root:
        if local(note) not in ("footnote", "endnote"):
            continue
        if wattr(note, "type") in ("separator", "continuationSeparator", "continuationNotice"):
            continue
        nid = wattr(note, "id") or "?"
        out.append((nid, walk_story(note, name=f"{kind} {nid}")))
    return out

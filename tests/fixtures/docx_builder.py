"""Programmatic DOCX fixtures.

Builds minimal but valid OOXML packages, including the Word review parts
(``comments.xml``, ``commentsExtended.xml``, ``commentsIds.xml``, footnotes) and tracked
changes, which python-docx cannot author. Used by the tests and by
``scripts/build_examples.py``.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
W15_NS = "http://schemas.microsoft.com/office/word/2012/wordml"
W16CID_NS = "http://schemas.microsoft.com/office/word/2016/wordml/cid"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

DEFAULT_DATE = "2024-03-01T09:00:00Z"


@dataclass
class Anchor:
    """Text covered by a comment range."""

    cid: int
    text: str


@dataclass
class Ins:
    text: str
    author: str = "Author"
    date: str = "2024-05-01T10:00:00Z"


@dataclass
class Del:
    text: str
    author: str = "Author"
    date: str = "2024-05-01T10:00:00Z"


@dataclass
class Bold:
    text: str


Segment = str | Anchor | Ins | Del | Bold


@dataclass
class _Comment:
    cid: int
    paragraphs: list[str]
    author: str
    initials: str
    date: str
    parent: int | None
    done: bool | None


@dataclass
class DocxBuilder:
    body: list[str] = field(default_factory=list)
    comments: list[_Comment] = field(default_factory=list)
    footnotes: list[tuple[int, list[Segment]]] = field(default_factory=list)
    title: str = ""
    with_comment_ids: bool = True
    _rev_id: int = 100

    # -- inline -------------------------------------------------------------------
    def _run(self, text: str, bold: bool = False, deleted: bool = False) -> str:
        tag = "w:delText" if deleted else "w:t"
        rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
        return f'<w:r>{rpr}<{tag} xml:space="preserve">{escape(text)}</{tag}></w:r>'

    def _segments(self, segments: tuple[Segment, ...] | list[Segment]) -> str:
        out: list[str] = []
        for seg in segments:
            if isinstance(seg, str):
                out.append(self._run(seg))
            elif isinstance(seg, Bold):
                out.append(self._run(seg.text, bold=True))
            elif isinstance(seg, Anchor):
                out.append(
                    f'<w:commentRangeStart w:id="{seg.cid}"/>{self._run(seg.text)}'
                    f'<w:commentRangeEnd w:id="{seg.cid}"/>'
                    f'<w:r><w:commentReference w:id="{seg.cid}"/></w:r>'
                )
            elif isinstance(seg, Ins):
                self._rev_id += 1
                out.append(
                    f'<w:ins w:id="{self._rev_id}" w:author="{escape(seg.author)}" w:date="{seg.date}">'
                    f"{self._run(seg.text)}</w:ins>"
                )
            elif isinstance(seg, Del):
                self._rev_id += 1
                out.append(
                    f'<w:del w:id="{self._rev_id}" w:author="{escape(seg.author)}" w:date="{seg.date}">'
                    f"{self._run(seg.text, deleted=True)}</w:del>"
                )
        return "".join(out)

    # -- blocks -------------------------------------------------------------------
    def heading(self, text: str, level: int = 1) -> DocxBuilder:
        self.body.append(
            f'<w:p><w:pPr><w:pStyle w:val="Heading{level}"/></w:pPr>{self._run(text)}</w:p>'
        )
        return self

    def title_para(self, text: str) -> DocxBuilder:
        self.body.append(f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>{self._run(text)}</w:p>')
        return self

    def para(
        self, *segments: Segment, style: str | None = None, numbered: bool = False
    ) -> DocxBuilder:
        ppr = ""
        if style or numbered:
            inner = f'<w:pStyle w:val="{style}"/>' if style else ""
            if numbered:
                inner += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
            ppr = f"<w:pPr>{inner}</w:pPr>"
        self.body.append(f"<w:p>{ppr}{self._segments(segments)}</w:p>")
        return self

    def empty_para(self) -> DocxBuilder:
        self.body.append("<w:p/>")
        return self

    def table(self, rows: list[list[str | list[Segment]]]) -> DocxBuilder:
        xml = ["<w:tbl><w:tblPr/><w:tblGrid/>"]
        for row in rows:
            xml.append("<w:tr>")
            for cell in row:
                segs: list[Segment] = [cell] if isinstance(cell, str) else cell
                xml.append(f"<w:tc><w:tcPr/><w:p>{self._segments(segs)}</w:p></w:tc>")
            xml.append("</w:tr>")
        xml.append("</w:tbl>")
        self.body.append("".join(xml))
        return self

    def raw(self, xml: str) -> DocxBuilder:
        self.body.append(xml)
        return self

    def comment(
        self,
        cid: int,
        text: str | list[str],
        author: str = "Prof. Smith",
        initials: str = "PS",
        date: str = DEFAULT_DATE,
        parent: int | None = None,
        done: bool | None = None,
    ) -> DocxBuilder:
        paragraphs = [text] if isinstance(text, str) else list(text)
        self.comments.append(_Comment(cid, paragraphs, author, initials, date, parent, done))
        return self

    def footnote(self, fid: int, *segments: Segment) -> DocxBuilder:
        self.footnotes.append((fid, list(segments)))
        return self

    # -- packaging ----------------------------------------------------------------
    @staticmethod
    def _para_id(cid: int) -> str:
        return f"{0x10000000 + cid * 0x10:08X}"

    def _styles_xml(self) -> str:
        def style(sid: str, name: str, extra: str = "") -> str:
            return (
                f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/>'
                f"{extra}</w:style>"
            )

        styles = [
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>',
            style("Title", "Title"),
            style("Caption", "caption"),
            style("ListParagraph", "List Paragraph"),
        ]
        for i in range(1, 4):
            styles.append(
                style(
                    f"Heading{i}", f"heading {i}", f'<w:pPr><w:outlineLvl w:val="{i - 1}"/></w:pPr>'
                )
            )
        return (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W_NS}">'
            + "".join(styles)
            + "</w:styles>"
        )

    def _comments_xml(self) -> str:
        items: list[str] = []
        for c in self.comments:
            paras = []
            for i, text in enumerate(c.paragraphs):
                pid = (
                    self._para_id(c.cid)
                    if i == len(c.paragraphs) - 1
                    else f"{0x20000000 + c.cid * 16 + i:08X}"
                )
                paras.append(
                    f'<w:p w14:paraId="{pid}"><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
                )
            items.append(
                f'<w:comment w:id="{c.cid}" w:author="{escape(c.author)}" w:date="{c.date}" '
                f'w:initials="{escape(c.initials)}">{"".join(paras)}</w:comment>'
            )
        return (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:comments xmlns:w="{W_NS}" xmlns:w14="{W14_NS}">{"".join(items)}</w:comments>'
        )

    def _comments_ex_xml(self) -> str:
        items = []
        for c in self.comments:
            attrs = f'w15:paraId="{self._para_id(c.cid)}"'
            if c.parent is not None:
                attrs += f' w15:paraIdParent="{self._para_id(c.parent)}"'
            attrs += f' w15:done="{1 if c.done else 0}"'
            items.append(f"<w15:commentEx {attrs}/>")
        return (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w15:commentsEx xmlns:w15="{W15_NS}">{"".join(items)}</w15:commentsEx>'
        )

    def _comments_ids_xml(self) -> str:
        items = [
            f'<w16cid:commentId w16cid:paraId="{self._para_id(c.cid)}" w16cid:durableId="{0x50000000 + c.cid:08X}"/>'
            for c in self.comments
        ]
        return (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w16cid:commentsIds xmlns:w16cid="{W16CID_NS}">{"".join(items)}</w16cid:commentsIds>'
        )

    def _footnotes_xml(self) -> str:
        items = [
            '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>',
            '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>',
        ]
        for fid, segs in self.footnotes:
            items.append(f'<w:footnote w:id="{fid}"><w:p>{self._segments(segs)}</w:p></w:footnote>')
        return (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:footnotes xmlns:w="{W_NS}">{"".join(items)}</w:footnotes>'
        )

    def document_xml(self) -> str:
        return (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{W_NS}" xmlns:w14="{W14_NS}"><w:body>{"".join(self.body)}</w:body></w:document>'
        )

    def parts(self) -> dict[str, str]:
        ct = [
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
            '<Default Extension="xml" ContentType="application/xml"/>',
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>',
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>',
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        ]
        rels = [
            f'<Relationship Id="rId1" Type="{DOC_REL}/styles" Target="styles.xml"/>',
        ]
        parts: dict[str, str] = {}
        if self.comments:
            ct.append(
                '<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
            )
            rels.append(
                f'<Relationship Id="rId2" Type="{DOC_REL}/comments" Target="comments.xml"/>'
            )
            parts["word/comments.xml"] = self._comments_xml()
            if any(c.parent is not None or c.done is not None for c in self.comments):
                ct.append(
                    '<Override PartName="/word/commentsExtended.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"/>'
                )
                rels.append(
                    '<Relationship Id="rId3" Type="http://schemas.microsoft.com/office/2011/relationships/commentsExtended" Target="commentsExtended.xml"/>'
                )
                parts["word/commentsExtended.xml"] = self._comments_ex_xml()
            if self.with_comment_ids:
                ct.append(
                    '<Override PartName="/word/commentsIds.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.commentsIds+xml"/>'
                )
                rels.append(
                    '<Relationship Id="rId4" Type="http://schemas.microsoft.com/office/2016/09/relationships/commentsIds" Target="commentsIds.xml"/>'
                )
                parts["word/commentsIds.xml"] = self._comments_ids_xml()
        if self.footnotes:
            ct.append(
                '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
            )
            rels.append(
                f'<Relationship Id="rId5" Type="{DOC_REL}/footnotes" Target="footnotes.xml"/>'
            )
            parts["word/footnotes.xml"] = self._footnotes_xml()
        title = escape(self.title)
        parts.update(
            {
                "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                + "".join(ct)
                + "</Types>",
                "_rels/.rels": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{REL_NS}">'
                f'<Relationship Id="rId1" Type="{DOC_REL}/officeDocument" Target="word/document.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                "</Relationships>",
                "word/_rels/document.xml.rels": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{REL_NS}">'
                + "".join(rels)
                + "</Relationships>",
                "word/document.xml": self.document_xml(),
                "word/styles.xml": self._styles_xml(),
                "docProps/core.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                f"<dc:title>{title}</dc:title></cp:coreProperties>",
            }
        )
        return parts

    def save(
        self, path: str | Path, overrides: dict[str, str | bytes | None] | None = None
    ) -> Path:
        """Write the package. ``overrides`` replaces (or, with ``None``, drops) parts."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        parts: dict[str, str | bytes] = dict(self.parts())
        for name, value in (overrides or {}).items():
            if value is None:
                parts.pop(name, None)
            else:
                parts[name] = value
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            order = ["[Content_Types].xml", "_rels/.rels"]
            for name in order + [n for n in parts if n not in order]:
                data = parts[name]
                zf.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
        return out

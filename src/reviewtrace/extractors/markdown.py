"""Parse a plain-text / Markdown reviewer report into headings, items and paragraphs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from reviewtrace.utils.errors import DocumentReadError
from reviewtrace.utils.text import strip_control

MAX_TEXT_BYTES = 5 * 1024 * 1024

_ATX = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(r"^(\s*)(?:(\d{1,3})[.)]|\((\d{1,3})\)|[-*+•])\s+(\S.*)$")
LABELLED = re.compile(
    r"^\s*(?:\*\*|__)?(?:reviewer\s+)?(?:comment|point|issue|concern|suggestion|recommendation|query|item|"
    r"r\d+(?:[.\-]\d+)?)\s*#?(\d{1,3})?\s*[:.)\-–](?:\*\*|__)?\s*(\S.*)$",
    re.I,
)
_BOLD_LINE = re.compile(r"^\s*(?:\*\*|__)(.+?)(?:\*\*|__)\s*:?\s*$")
_RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
COMMENT_COLUMN = re.compile(r"comment|issue|feedback|concern|suggestion|request|point|query", re.I)
LOCATION_COLUMN = re.compile(r"section|location|where|page|line|ref", re.I)
_MD_EMPHASIS = re.compile(r"(\*\*|__|`)")


@dataclass
class Block:
    kind: str  # "heading" | "item" | "paragraph"
    text: str
    level: int = 0
    label: str | None = None
    extra: str = ""  # location column value for table rows


def clean_inline(text: str) -> str:
    return re.sub(r"\s+", " ", _MD_EMPHASIS.sub("", text)).strip()


def read_text_file(path_str: str) -> str:
    from pathlib import Path

    path = Path(path_str)
    if not path.exists():
        raise DocumentReadError(f"file not found: {path}")
    if not path.is_file():
        raise DocumentReadError(f"not a file: {path}")
    if path.stat().st_size > MAX_TEXT_BYTES:
        raise DocumentReadError(
            f"{path.name}: text file is larger than {MAX_TEXT_BYTES // (1024 * 1024)} MB"
        )
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    return strip_control(text)


def _split_row(line: str) -> list[str]:
    inner = line.strip().strip("|")
    return [c.strip() for c in inner.split("|")]


def parse_blocks(text: str) -> list[Block]:
    blocks: list[Block] = []
    lines = text.splitlines()
    para: list[str] = []
    item: Block | None = None
    item_indent = 0
    i = 0

    def flush_para() -> None:
        nonlocal para
        if para:
            blocks.append(Block("paragraph", clean_inline(" ".join(para))))
            para = []

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        i += 1
        if not line.strip():
            flush_para()
            item = None
            continue
        if _RULE.match(line):
            flush_para()
            item = None
            continue
        atx = _ATX.match(line)
        if atx:
            flush_para()
            item = None
            blocks.append(Block("heading", clean_inline(atx.group(2)), level=len(atx.group(1))))
            continue
        if _TABLE_ROW.match(line):
            flush_para()
            item = None
            rows = [line]
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                rows.append(lines[i].rstrip())
                i += 1
            blocks.extend(_table_blocks(rows))
            continue
        bold = _BOLD_LINE.match(line)
        if bold and len(bold.group(1).split()) <= 8 and not _ITEM.match(line):
            flush_para()
            item = None
            blocks.append(Block("heading", clean_inline(bold.group(1)), level=3))
            continue
        m = _ITEM.match(line)
        labelled = LABELLED.match(line)
        if m or labelled:
            indent = len(m.group(1).expandtabs(4)) if m else 0
            if m and item is not None and indent >= item_indent + 2 and item.label:
                item.text = clean_inline(
                    f"{item.text} {m.group(4)}"
                )  # nested bullet: part of the parent item
                continue
            flush_para()
            if m:
                label = m.group(2) or m.group(3)
                body = m.group(4)
            else:
                assert labelled is not None
                label, body = labelled.group(1), labelled.group(2)
            item = Block("item", clean_inline(body), label=label)
            item_indent = indent
            blocks.append(item)
            continue
        if item is not None:
            item.text = clean_inline(f"{item.text} {line}")
            continue
        para.append(line)
    flush_para()
    return blocks


def _table_blocks(rows: list[str]) -> list[Block]:
    parsed = [_split_row(r) for r in rows if not _TABLE_SEP.match(r)]
    if not parsed:
        return []
    header, body = parsed[0], parsed[1:]
    return table_rows_to_blocks(header, body)


def table_rows_to_blocks(header: list[str], body: list[list[str]]) -> list[Block]:
    col = next((i for i, h in enumerate(header) if COMMENT_COLUMN.search(h)), None)
    if col is None:
        return [Block("paragraph", clean_inline(" ".join(r))) for r in body if any(r)]
    loc_col = next(
        (i for i, h in enumerate(header) if LOCATION_COLUMN.search(h) and i != col), None
    )
    out: list[Block] = []
    for n, row in enumerate(body, start=1):
        if col >= len(row) or not row[col].strip():
            continue
        label = (
            row[0].strip().rstrip(".)")
            if row and row[0].strip().rstrip(".)").isdigit() and col != 0
            else str(n)
        )
        extra = row[loc_col].strip() if loc_col is not None and loc_col < len(row) else ""
        out.append(Block("item", clean_inline(row[col]), label=label, extra=clean_inline(extra)))
    return out

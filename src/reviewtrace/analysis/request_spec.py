"""Extract checkable specifics from the wording of a reviewer request.

Everything returned here is read directly from the comment text (numbers, quoted strings,
"Table 4.2", "since 2020"). Nothing is inferred beyond what the reviewer wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from reviewtrace.analysis.classifier import strip_lead
from reviewtrace.utils.text import FILLER, STOPWORDS, content_terms, normalize, tokens

_I = re.I
_NUM = r"\$?\d[\d,]*(?:\.\d+)?%?"

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}  # fmt: skip

_QUOTED = re.compile(r"[\"“]([^\"”]{2,200})[\"”]|(?<![\w])[‘']([^'’]{2,200})[’'](?![\w])")
_REPLACE_QUOTED = re.compile(
    r"(?:change|replace|rename|retitle|revise|substitute|amend)\s+(?:the\s+(?:heading|title|word|term|phrase|text|name)\s+)?"
    r"[\"“‘']([^\"”’']+)[\"”’']\s+(?:to|with|by|into|as)\s+"
    r"[\"“‘']([^\"”’']+)[\"”’']",
    _I,
)
_SHOULD_READ_QUOTED = re.compile(
    r"[\"“‘']([^\"”’']+)[\"”’']\s+should\s+(?:be|read|say)\s+"
    r"[\"“‘']([^\"”’']+)[\"”’']",
    _I,
)
_INSTEAD_QUOTED = re.compile(
    r"(?:use\s+)?[\"“‘']([^\"”’']+)[\"”’']\s+(?:instead of|rather than)\s+"
    r"[\"“‘']([^\"”’']+)[\"”’']",
    _I,
)
_REPLACE_PLAIN = re.compile(r"\breplace\s+(.{2,60}?)\s+with\s+(.{2,60}?)\s*[.?!]?$", _I)

_NUM_SHOULD_NOT = re.compile(
    rf"should\s+(?:be|read|say|have been)\s+({_NUM})\s*,?\s*(?:and\s+)?not\s+({_NUM})", _I
)
_NUM_INSTEAD = re.compile(rf"({_NUM})\s*,?\s*(?:instead of|rather than|and not|not)\s+({_NUM})", _I)
_NUM_CHANGE = re.compile(
    rf"(?:change|replace|update|correct|revise)\s+({_NUM})\s+(?:to|with|by|into)\s+({_NUM})", _I
)
_NUM_FROM_TO = re.compile(rf"from\s+({_NUM})\s+to\s+({_NUM})", _I)
_NUM_SHOULD_BE = re.compile(
    rf"(?:should\s+(?:be|read|say)|must\s+be|is\s+actually|are\s+actually)\s+({_NUM})", _I
)

_TABLE_REF = re.compile(r"\btables?\s+([A-Za-z]?\d+(?:[.\-]\d+)*)\b", _I)
_FIGURE_REF = re.compile(r"\b(?:figures?|fig\.)\s*([A-Za-z]?\d+(?:[.\-]\d+)*)\b", _I)
_SECTION_REF = re.compile(
    r"(?:\bsections?|\bsubsections?|\bsect\.|§|\bchapter)\s*(\d+(?:\.\d+)*)", _I
)
_PARAGRAPH_REF = re.compile(r"\bparagraphs?\s+(\d+)\b", _I)
_PAGE_REF = re.compile(r"\b(?:page|pp?\.)\s*(\d+)\b", _I)
_MIN_COUNT = re.compile(
    r"(?:at least|minimum of|no fewer than|not fewer than)\s+(\d+|"
    + "|".join(_WORD_NUMBERS)
    + r")\b",
    _I,
)
_COUNT_OF = re.compile(
    r"\b(\d+|" + "|".join(_WORD_NUMBERS) + r")\s+(?:more\s+|additional\s+|new\s+|recent\s+)*"
    r"(?:references?|citations?|sources?|studies|papers|articles|publications)\b",
    _I,
)
_SINCE = re.compile(
    r"(?:since|after|from|post-?|published (?:after|since|from|in or after)|newer than|later than)\s+"
    r"(?:the year\s+)?((?:19|20)\d{2})\b",
    _I,
)
_SINCE_ONWARD = re.compile(r"((?:19|20)\d{2})\s*(?:onwards?|or later|and later|and after|\+)", _I)
_BETWEEN = re.compile(r"between\s+((?:19|20)\d{2})\s+and\s+((?:19|20)\d{2})", _I)
_STYLE = re.compile(
    r"\b(apa|harvard|mla|vancouver|chicago|ieee|ama|turabian)\b(?:\s*(\d+)(?:th|st|nd|rd)?)?", _I
)
_DEST = re.compile(
    r"\b(?:to|into|under|after|before)\s+(?:the\s+)?(?:(?:sub)?section\s+|chapter\s+|§\s*)(\d+(?:\.\d+)*)",
    _I,
)
_HEAD_CUT = re.compile(
    r"\b(?:of|for|in|on|to|at|within|from|by|with|about|regarding|concerning|that|which|how|why)\b",
    _I,
)
_DETERMINERS = frozenset(
    {"the", "a", "an", "any", "each", "every", "some", "its", "their", "this", "these", "those"}
)
_COORD_SPLIT = re.compile(r"\s*(?:,\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+|\s*&\s*)", _I)


@dataclass
class RequestSpec:
    text: str
    terms: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    replace_pair: tuple[str, str] | None = None
    numeric_correction: tuple[str | None, str] | None = None
    table_refs: list[str] = field(default_factory=list)
    figure_refs: list[str] = field(default_factory=list)
    section_refs: list[str] = field(default_factory=list)
    paragraph_refs: list[int] = field(default_factory=list)
    page_refs: list[int] = field(default_factory=list)
    min_count: int | None = None
    since_year: int | None = None
    until_year: int | None = None
    concepts: list[list[str]] = field(default_factory=list)
    concept_labels: list[str] = field(default_factory=list)
    destination_section: str | None = None
    style_name: str | None = None

    @property
    def has_explicit_reference_criteria(self) -> bool:
        return self.min_count is not None or self.since_year is not None


def _to_int(token: str) -> int:
    t = token.lower()
    return _WORD_NUMBERS[t] if t in _WORD_NUMBERS else int(t)


def _clean_number(n: str) -> str:
    return n.replace(",", "").replace("$", "")


def _concept_groups(text: str) -> tuple[list[list[str]], list[str]]:
    """Coordinated noun phrases such as "validity and reliability of the scale"."""
    body = re.sub(r"^\W*(?:[A-Za-z]+)\s+", "", strip_lead(text), count=1)  # drop the leading verb
    fragments = [f for f in _COORD_SPLIT.split(body) if f.strip()]
    if len(fragments) < 2 or len(fragments) > 5:
        return [], []
    groups: list[list[str]] = []
    labels: list[str] = []
    for i, frag in enumerate(fragments):
        head = _HEAD_CUT.split(frag, maxsplit=1)[0]
        words = [w for w in tokens(head) if w not in _DETERMINERS]
        # A coordinated list of concepts is short noun phrases; anything wordier is a clause.
        if not (1 <= len(words) <= 3) or (i < len(fragments) - 1 and _HEAD_CUT.search(frag)):
            return [], []
        stems = content_terms(" ".join(words))
        if not stems:
            return [], []
        groups.append(stems)
        labels.append(" ".join(words))
    return groups, labels


def parse_request(text: str, want_concepts: bool = False) -> RequestSpec:
    text = text.strip()[:2000]
    spec = RequestSpec(text=text)

    quoted: list[str] = []
    for qm in _QUOTED.finditer(text):
        q = (qm.group(1) or qm.group(2) or "").strip()
        if q:
            quoted.append(normalize(q))
    spec.phrases = quoted

    for pattern in (_REPLACE_QUOTED, _SHOULD_READ_QUOTED):
        m = pattern.search(text)
        if m:
            spec.replace_pair = (m.group(1).strip(), m.group(2).strip())
            break
    else:
        m = _INSTEAD_QUOTED.search(text)
        if m:
            spec.replace_pair = (m.group(2).strip(), m.group(1).strip())
        else:
            m = _REPLACE_PLAIN.search(text)
            if m and len(m.group(1).split()) <= 6 and len(m.group(2).split()) <= 6:
                spec.replace_pair = (m.group(1).strip(" \"'"), m.group(2).strip(" \"'"))

    m2: re.Match[str] | None = _NUM_SHOULD_NOT.search(text)
    if m2:
        spec.numeric_correction = (_clean_number(m2.group(2)), _clean_number(m2.group(1)))
    else:
        m2 = _NUM_CHANGE.search(text) or _NUM_FROM_TO.search(text)
        if m2:
            spec.numeric_correction = (_clean_number(m2.group(1)), _clean_number(m2.group(2)))
        else:
            m2 = _NUM_INSTEAD.search(text)
            if m2:
                spec.numeric_correction = (_clean_number(m2.group(2)), _clean_number(m2.group(1)))
            else:
                m2 = _NUM_SHOULD_BE.search(text)
                if m2:
                    spec.numeric_correction = (None, _clean_number(m2.group(1)))

    spec.numbers = [_clean_number(n) for n in re.findall(_NUM, text)]
    spec.table_refs = [f"Table {n}" for n in _TABLE_REF.findall(text)]
    spec.figure_refs = [f"Figure {n}" for n in _FIGURE_REF.findall(text)]
    spec.section_refs = _SECTION_REF.findall(text)
    spec.paragraph_refs = [int(n) for n in _PARAGRAPH_REF.findall(text)]
    spec.page_refs = [int(n) for n in _PAGE_REF.findall(text)]

    mc = _MIN_COUNT.search(text) or _COUNT_OF.search(text)
    if mc:
        spec.min_count = _to_int(mc.group(1))
    ms = _SINCE.search(text) or _SINCE_ONWARD.search(text)
    if ms:
        spec.since_year = int(ms.group(1))
    mb = _BETWEEN.search(text)
    if mb:
        spec.since_year, spec.until_year = int(mb.group(1)), int(mb.group(2))

    mst = _STYLE.search(text)
    if mst:
        spec.style_name = mst.group(0).strip()
    md = _DEST.search(text)
    if md:
        spec.destination_section = md.group(1)

    generic = FILLER | STOPWORDS
    spec.terms = [t for t in content_terms(text) if t not in generic]
    if want_concepts:
        spec.concepts, spec.concept_labels = _concept_groups(text)
    return spec

"""Conservative, rule-based categorisation of review comments.

The category selects which evidence rules apply. It is deliberately not clever: when no
rule matches the comment is ``OTHER`` and the assessor falls back to structural evidence
only. Compound comments are split into sub-requirements so that resolving one part can
never mark the whole comment resolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from reviewtrace.models.issue import IssueCategory, Severity, SubRequirement
from reviewtrace.utils.text import sentences

C = IssueCategory
MAX_ANALYSIS_CHARS = 2000
MAX_PARTS = 8

_I = re.I

# (pattern, category). The earliest match in the comment picks the primary category.
_ACTION_RULES: list[tuple[re.Pattern[str], IssueCategory]] = [
    (
        re.compile(r"\b(remove|delete|omit|drop|eliminate|take out|get rid of|strike|cut)\b", _I),
        C.REMOVE,
    ),
    (re.compile(r"\b(replace|substitute|swap|rename|retitle)\b", _I), C.REPLACE),
    (re.compile(r"\bchange\b(?=[^.?!]*\b(?:to|into|with|from)\b)", _I), C.REPLACE),
    (re.compile(r"\b(move|relocate|reposition|shift)\b", _I), C.MOVE),
    (re.compile(r"\b(merge|combine|consolidate)\b", _I), C.MERGE),
    (re.compile(r"\b(split|divide|break up|separate)\b", _I), C.SPLIT),
    (
        re.compile(r"\b(restructure|reorgani[sz]e|reorder|rearrange|re-?sequence)\b", _I),
        C.RESTRUCTURE,
    ),
    (
        re.compile(
            r"\b(expand|elaborate|extend|flesh out|develop further|(?:more|greater|further) (?:detail|depth))\b"
            r"|\bin (?:more|greater) (?:detail|depth)\b",
            _I,
        ),
        C.EXPAND,
    ),
    (re.compile(r"\b(cite|citations?|attribute)\b", _I), C.CITE),
    (re.compile(r"\b(update|revise|refresh|modernis\w+|modernize\w*)\b", _I), C.UPDATE),
    (
        re.compile(
            r"\b(correct|fix|amend|rectify)\b|\bshould (?:be|read|say)\b|\b(?:is|are) (?:wrong|incorrect|inaccurate)\b"
            r"|\btypos?\b|\bmistake\b",
            _I,
        ),
        C.CORRECT,
    ),
    (
        re.compile(
            r"\b(explain|justify|account for|elucidate|discuss|define|describe|outline|rationale for|"
            r"demonstrate|illustrate|relates?|link|connect|show (?:how|why))\b",
            _I,
        ),
        C.EXPLAIN,
    ),
    (
        re.compile(
            r"\b(clarify|clarification|rephrase|reword|make clear|unclear|not clear|ambiguous|specify|"
            r"what do you mean|what is meant)\b",
            _I,
        ),
        C.CLARIFY,
    ),
    (
        re.compile(
            r"\b(add|include|provide|insert|incorporate|introduce|state|mention|supply|give|report|"
            r"present(?!\s+(?:study|research|work|paper|investigation|thesis|analysis|context))|"
            r"list|quantify|missing|lacks?)\b",
            _I,
        ),
        C.ADD,
    ),
    (
        re.compile(r"\b(verify|double-?check|confirm|validate|check|ensure|make sure)\b", _I),
        C.VERIFY,
    ),
    (re.compile(r"\b(inconsisten\w*|consisten\w*|uniform\w*|throughout)\b", _I), C.CONSISTENCY),
    (
        re.compile(
            r"\b(format\w*|font|spacing|margins?|indent\w*|typeface|line spacing|heading style|"
            r"apa|harvard|mla|vancouver|chicago|ieee)\b",
            _I,
        ),
        C.REFORMAT,
    ),
]

_REFERENCE_TOPIC = re.compile(
    r"\b(references?|reference list|bibliograph\w*|literature|sources|citations?)\b", _I
)
_TABLE_TOPIC = re.compile(r"\btables?\b", _I)
_FIGURE_TOPIC = re.compile(r"\b(figures?|fig\.|charts?|graphs?|diagrams?|images?)\b", _I)
_STAT_TOPIC = re.compile(
    r"\b(p[- ]?values?|confidence intervals?|effect sizes?|standard deviation|regression|anova|t-?test|"
    r"chi[- ]?square|significan\w+|correlation|statistic\w*|odds ratio|degrees of freedom|cronbach\w*|"
    r"reliability coefficient|power analysis)\b",
    _I,
)
_STYLE_TOPIC = re.compile(r"\b(apa|harvard|mla|vancouver|chicago|ieee|ama|turabian)\b", _I)
_SUBJECTIVE = re.compile(
    r"\b(more (?:convincing|persuasive|rigorous|robust|compelling|critical|nuanced|coherent|concise|"
    r"precise|professional|academic)|convincing|persuasive|stronger|weaker|weak|awkward|flow|tone|"
    r"readab\w*|clearer|improv\w*|polish\w*|better|sloppy|vague|rigor\w*|compelling)\b",
    _I,
)
_DIRECTIVE_HINT = re.compile(
    r"\b(should|must|need(?:s|ed)? to|needs|please|consider|suggest\w*|recommend\w*|would like|"
    r"could you|can you|would be (?:better|helpful|useful)|why|how|what|which|missing|lack\w*|"
    r"unclear|not clear|too (?:short|long|vague|brief)|insufficient|inadequate|incomplete|"
    r"required|require\w*|ought)\b",
    _I,
)
_PRAISE = re.compile(
    r"^\W*(good|nice|great|excellent|fine|agree[d]?|ok(?:ay)?|well done|thanks?|noted|interesting|"
    r"correct|right|yes|perfect|lgtm|good point|makes sense)\b[\W\w]{0,40}$",
    _I,
)
_YESNO_QUESTION = re.compile(r"^\W*(is|are|does|do|did|was|were|has|have)\b[^?]*\?\W*$", _I)
_WH_QUESTION = re.compile(r"^\W*(why|how|what|which|where)\b[^?]*\?\W*$", _I)

_SEV_MAJOR = re.compile(r"\b(major|critical|essential|serious|fundamental|mandatory|must)\b", _I)
_SEV_MINOR = re.compile(r"\b(minor|trivial|typos?|cosmetic|nitpick|small)\b", _I)
_SEV_SUGGEST = re.compile(r"\b(consider|suggest\w*|optional|perhaps|you might|may want to)\b", _I)

_LEAD_IN = re.compile(
    r"^\W*(?:please\s+|kindly\s+)?"
    r"(?:(?:could|can|would|will) you(?: please)?\s+|"
    r"(?:i|we) (?:would |'d )?(?:suggest|recommend|ask|advise|propose|request)(?: that)?(?: you| the authors?)?\s+(?:to\s+)?|"
    r"(?:you|the authors?|the author|authors?|the candidate|the student) (?:should|must|need to|needs to|might want to|may want to|could|ought to|have to)\s+|"
    r"it (?:would be|is) (?:better|helpful|useful|advisable|necessary) (?:to|if you)\s+|"
    r"(?:you )?(?:need|needs) to\s+|"
    r"please\s+)",
    _I,
)

ACTION_VERBS = (
    "define|explain|show|describe|discuss|add|include|provide|justify|clarify|expand|elaborate|"
    "remove|delete|correct|fix|replace|update|revise|cite|state|mention|specify|compare|contrast|"
    "summari[sz]e|outline|list|report|present|demonstrate|illustrate|link|relate|connect|address|"
    "rewrite|reword|rephrase|move|merge|split|restructure|verify|check|ensure|quantify|support|"
    "indicate|identify|acknowledge|introduce|reference|give|supply|insert|incorporate|consider|"
    "reorder|rename|separate|combine|conclude|analyse|analyze|evaluate|elucidate|examine"
)
_VERB_START = f"(?:{ACTION_VERBS})"
_CLAUSE_SPLIT = re.compile(
    rf"\s*(?:,\s*(?:and\s+|then\s+)?|;\s*|\s+and\s+then\s+|\s+then\s+|\s+and\s+)(?={_VERB_START}\b)",
    _I,
)
_ENUM_MARK = re.compile(r"(?:^|\s)\(?([a-e]|[1-6])[.)]\s+(?=\S)", _I)
_VERB_RE = re.compile(rf"^{_VERB_START}\b", _I)


@dataclass
class Classification:
    category: IssueCategory
    secondary: list[IssueCategory] = field(default_factory=list)
    requested_action: str = ""
    actionable: bool = True
    subjective: bool = False


def strip_lead(sentence: str) -> str:
    prev = None
    s = sentence.strip()
    while prev != s:
        prev = s
        s = _LEAD_IN.sub("", s, count=1).strip()
    return s


def classify(comment: str) -> Classification:
    """Categorise ``comment``. Only the first ``MAX_ANALYSIS_CHARS`` characters are examined."""
    text = comment.strip()[:MAX_ANALYSIS_CHARS]
    if not text:
        return Classification(C.OTHER, actionable=False)
    hits: list[tuple[int, int, IssueCategory]] = []
    for prio, (pattern, cat) in enumerate(_ACTION_RULES):
        m = pattern.search(text)
        if m:
            hits.append((m.start(), prio, cat))
    hits.sort()
    subjective = bool(_SUBJECTIVE.search(text))
    directive = bool(_DIRECTIVE_HINT.search(text))

    secondary: list[IssueCategory]
    primary: IssueCategory
    if _YESNO_QUESTION.match(text) and not any(
        c in (C.REMOVE, C.ADD, C.EXPAND) for _, _, c in hits[:1]
    ):
        primary = C.VERIFY
        secondary = [c for _, _, c in hits if c is not C.VERIFY]
    elif hits:
        primary = hits[0][2]
        secondary = [c for _, _, c in hits[1:] if c is not primary]
    elif _WH_QUESTION.match(text):
        primary, secondary = C.EXPLAIN, []
    else:
        primary, secondary = C.OTHER, []

    # Topic-driven refinements: the object of the request decides the evidence rule.
    if primary in (
        C.ADD,
        C.UPDATE,
        C.CORRECT,
        C.EXPAND,
        C.VERIFY,
        C.EXPLAIN,
        C.OTHER,
    ) and _REFERENCE_TOPIC.search(text):
        if primary is C.VERIFY and _STYLE_TOPIC.search(text):
            primary = C.REFORMAT
        elif primary in (C.ADD, C.UPDATE, C.CORRECT, C.OTHER) and re.search(
            r"\b(references?|reference list|bibliograph\w*|literature|sources)\b", text, _I
        ):
            primary = C.REFERENCE
    if primary in (C.CORRECT, C.UPDATE, C.ADD, C.VERIFY, C.REPLACE) and _TABLE_TOPIC.search(text):
        primary = C.TABLE
    elif primary in (C.CORRECT, C.UPDATE, C.ADD, C.VERIFY, C.REPLACE) and _FIGURE_TOPIC.search(
        text
    ):
        primary = C.FIGURE
    elif primary in (C.CORRECT, C.VERIFY, C.UPDATE, C.OTHER) and _STAT_TOPIC.search(text):
        primary = C.STATISTICAL
    if (
        primary is C.REFORMAT
        and not _STYLE_TOPIC.search(text)
        and secondary
        and secondary[0] in (C.ADD, C.EXPLAIN)
    ):
        primary = secondary[0]

    actionable = primary is not C.OTHER or directive or subjective
    if primary is C.OTHER and _PRAISE.match(text) and not directive:
        actionable = False
    if not actionable:
        primary = C.OTHER

    return Classification(
        category=primary,
        secondary=secondary[:3],
        requested_action=_requested_action(text, primary),
        actionable=actionable,
        subjective=subjective,
    )


def _requested_action(text: str, category: IssueCategory) -> str:
    sents = sentences(text) or [text]
    chosen = sents[0]
    for s in sents:
        if any(p.search(s) for p, _ in _ACTION_RULES) or _DIRECTIVE_HINT.search(s):
            chosen = s
            break
    chosen = strip_lead(chosen)
    chosen = chosen[:1].upper() + chosen[1:] if chosen else chosen
    return chosen[:200].strip()


def detect_severity(text: str, group_hint: Severity | None = None) -> Severity | None:
    if group_hint is not None:
        return group_hint
    head = text[:MAX_ANALYSIS_CHARS]
    if _SEV_MINOR.search(head):
        return Severity.MINOR
    if _SEV_SUGGEST.search(head):
        return Severity.SUGGESTION
    if _SEV_MAJOR.search(head):
        return Severity.MAJOR
    return None


def _split_enumeration(sentence: str) -> list[str]:
    marks = list(_ENUM_MARK.finditer(sentence))
    if len(marks) < 2:
        return [sentence]
    parts: list[str] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(sentence)
        parts.append(sentence[m.end() : end].strip(" ;,"))
    lead = sentence[: marks[0].start()].strip()
    return [f"{lead} {p}".strip() if lead and not _VERB_RE.match(p) else p for p in parts if p]


def decompose(comment: str) -> list[str]:
    """Split a compound request into separately checkable requirements.

    Returns ``[]`` when the comment is not compound. A clause is only split off when it
    starts with an action verb, so "validity and reliability" stays a single requirement.
    """
    text = comment.strip()[:MAX_ANALYSIS_CHARS]
    clauses: list[str] = []
    for sent in sentences(text):
        for piece in _split_enumeration(sent):
            piece = strip_lead(piece).rstrip(".?! ")
            if not piece:
                continue
            for part in _CLAUSE_SPLIT.split(piece):
                part = part.strip(" ,;")
                if part:
                    clauses.append(part)
    directive = [c for c in clauses if _VERB_RE.match(c)]
    if len(directive) < 2:
        return []
    # A bare verb ("Explain") borrows the object of the clause that follows it.
    fixed: list[str] = []
    for i, clause in enumerate(directive):
        if len(clause.split()) == 1 and i + 1 < len(directive):
            following = directive[i + 1].split(None, 1)
            if len(following) == 2:
                clause = f"{clause} {following[1]}"
        fixed.append(clause[:1].upper() + clause[1:])
    return fixed[:MAX_PARTS]


def build_sub_requirements(issue_id: str, comment: str) -> list[SubRequirement]:
    parts = decompose(comment)
    return [
        SubRequirement(id=f"{issue_id}.{i}", text=part, category=classify(part).category)
        for i, part in enumerate(parts, start=1)
    ]


def looks_like_request(sentence: str) -> bool:
    """True for sentences that read as a request (used to filter prose review reports)."""
    s = sentence.strip()[:MAX_ANALYSIS_CHARS]
    return bool(_DIRECTIVE_HINT.search(s) or _VERB_RE.match(strip_lead(s)))

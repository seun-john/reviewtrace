"""Text helpers shared by the diff and analysis layers.

Everything here is deterministic and dependency-free so that results are
reproducible and explainable.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_WS = re.compile(r"\s+")
_TRANSLATE = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "‑": "-",
        " ": " ",
        "​": "",
        "­": "",
    }
)
_WORD = re.compile(r"[^\W_]+(?:['.\-][^\W_]+)*", re.UNICODE)
_SENT_SPLIT = re.compile(
    r"(?<=[.!?])(?<!et al\.)(?<!e\.g\.)(?<!i\.e\.)(?<!Fig\.)(?<!Dr\.)(?<!vs\.)(?<!No\.)"
    r"\s+(?=[A-Z0-9\"'(\[])"
)
_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?%?")


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


STOPWORDS = _words(
    """
    a an the and or but if then else of in on at to for from by with without into onto over
    under about as is are was were be been being am do does did done have has had having it
    its this that these those there here which who whom whose what when where why how not no
    nor so than too very can could should would will shall may might must also just only
    such each every any some all both either neither more most much many few less least own
    same other another their theirs them they we our ours us you your yours he she his her
    hers i me my mine one two per via etc
    """
)

# Words reviewers use to frame a request that say nothing about the content wanted.
FILLER = _words(
    """
    please kindly major main key important further additional additionally extra detail
    details detailed adequately adequate sufficient sufficiently properly appropriate
    appropriately section paragraph text sentence part chapter document draft thesis paper
    manuscript article present current authors author reviewer comment comments need needs
    needed want wanted ensure make sure consider suggest recommend recommended really
    clearly better good well add added include included provide provided show shown explain
    explained discuss discussed describe described clarify clarified expand expanded
    elaborate state mention give given define defined justify justified indicate indicated
    note noted address addressed update updated revise revised correct corrected fix fixed
    remove removed delete deleted replace replaced change changed put place placed use used
    using made try tried way ways thing things something anything bit lot like also still
    already now concerning regarding relating related respect pertaining
    """
)


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def strip_control(text: str) -> str:
    """Remove control characters (including ESC) so text is safe to print to a terminal."""
    return _CONTROL.sub("", text)


def normalize(text: str) -> str:
    """Lower-case, unify punctuation variants and collapse whitespace."""
    return _WS.sub(" ", text.translate(_TRANSLATE)).strip().lower()


def tokens(text: str) -> list[str]:
    """Word tokens from normalised text (keeps internal ``.``, ``'`` and ``-``)."""
    return _WORD.findall(normalize(text))


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def similarity(a: str, b: str) -> float:
    """Token-level similarity in [0, 1]; identical normalised text gives exactly 1.0."""
    ta, tb = tokens(a), tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    matcher = SequenceMatcher(None, ta, tb, autojunk=False)
    if matcher.real_quick_ratio() < 0.2 or matcher.quick_ratio() < 0.2:
        return matcher.quick_ratio() * 0.5
    return matcher.ratio()


def char_similarity(a: str, b: str) -> float:
    """Character-level similarity, suited to short strings such as headings."""
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb, autojunk=False).ratio()


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def token_set(text: str) -> frozenset[str]:
    return frozenset(t for t in tokens(text) if t not in STOPWORDS)


def sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text.strip())]
    return [p for p in parts if p]


def numbers_in(text: str) -> list[str]:
    """Numeric literals as written (``396``, ``0.86``, ``45%``), thousands separators removed."""
    return [m.group(0).replace(",", "") for m in _NUMBER.finditer(text)]


def contains_number(text: str, number: str) -> int:
    """Count exact occurrences of ``number`` (so ``39`` does not match inside ``396``)."""
    target = number.replace(",", "")
    return sum(1 for n in numbers_in(text) if n == target)


def stem(word: str) -> str:
    """A deliberately small suffix stripper. Good enough to pair ``sampling``/``sample``."""
    w = word.lower()
    if len(w) <= 3:
        return w
    for suffix, replacement in (
        ("ies", "y"),
        ("ations", "ate"),
        ("ation", "ate"),
        ("ities", ""),
        ("ity", ""),
        ("ings", ""),
        ("ing", ""),
        ("edly", ""),
        ("ed", ""),
        ("ness", ""),
        ("ments", ""),
        ("ment", ""),
        ("ly", ""),
        ("es", ""),
        ("s", ""),
    ):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            w = w[: len(w) - len(suffix)] + replacement
            break
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]
    return w


def content_terms(text: str, extra_stop: frozenset[str] = frozenset()) -> list[str]:
    """Ordered, de-duplicated stems of the informative words in ``text``."""
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens(text):
        if tok in STOPWORDS or tok in FILLER or tok in extra_stop or tok.isdigit() or len(tok) < 3:
            continue
        s = stem(tok)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def stems_in(text: str) -> frozenset[str]:
    return frozenset(stem(t) for t in tokens(text))


def truncate(text: str, limit: int) -> str:
    text = _WS.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"

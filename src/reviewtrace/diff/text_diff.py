"""Word-level text comparison used to describe what changed inside a paragraph."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from reviewtrace.utils.text import normalize, sentences, similarity

_EDGE_PUNCT = re.compile(r"^[\W_]+|[\W_]+$")


@dataclass
class WordDiff:
    ratio: float
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def added_text(self) -> str:
        return " ".join(self.added)

    @property
    def removed_text(self) -> str:
        return " ".join(self.removed)


def _word_key(word: str) -> str:
    return _EDGE_PUNCT.sub("", normalize(word))


def diff_words(old: str, new: str) -> WordDiff:
    """Spans inserted into / removed from ``old`` to obtain ``new`` (original casing kept)."""
    a, b = old.split(), new.split()
    ka, kb = [_word_key(w) for w in a], [_word_key(w) for w in b]
    sm = SequenceMatcher(None, ka, kb, autojunk=False)
    out = WordDiff(ratio=sm.ratio() if ka or kb else 1.0)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "insert"):
            out.added.append(" ".join(b[j1:j2]))
        if tag in ("replace", "delete"):
            out.removed.append(" ".join(a[i1:i2]))
    return out


def added_sentences(old: str, new: str, threshold: float = 0.85) -> list[str]:
    """Sentences of ``new`` that have no close counterpart in ``old``."""
    old_sents = sentences(old)
    fresh: list[str] = []
    for s in sentences(new):
        if not any(similarity(s, o) >= threshold for o in old_sents):
            fresh.append(s)
    return fresh

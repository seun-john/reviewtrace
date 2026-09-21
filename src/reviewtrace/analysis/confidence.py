"""Confidence is kept separate from status.

Status says *what* ReviewTrace concluded; confidence says how much the method behind that
conclusion can be trusted. Two inputs decide it:

* how strong the rule is (an exact check, a lexical check, or structural evidence only), and
* how well the request could be tied to a place in the document.
"""

from __future__ import annotations

from enum import IntEnum

from reviewtrace.models.finding import Confidence, Status


class RuleStrength(IntEnum):
    STRUCTURAL = 1  # something changed / did not change; adequacy is not checked
    LEXICAL = 2  # requested terms were (not) found in the new text
    DETERMINISTIC = 3  # an exact, checkable condition (value, heading, removal, count)


class TargetQuality(IntEnum):
    NONE = 0  # the request could not be tied to a location
    SECTION = 1  # a section was identified (named, referenced or inferred)
    FUZZY = 2  # the anchor text was found approximately or ambiguously
    EXACT = 3  # the anchored paragraph(s) were located exactly


def decide(strength: RuleStrength, quality: TargetQuality) -> Confidence:
    if strength is RuleStrength.DETERMINISTIC and quality >= TargetQuality.SECTION:
        return Confidence.HIGH
    if strength is RuleStrength.DETERMINISTIC:
        return Confidence.MEDIUM
    if strength is RuleStrength.LEXICAL and quality >= TargetQuality.SECTION:
        return Confidence.MEDIUM
    if strength is RuleStrength.STRUCTURAL and quality >= TargetQuality.FUZZY:
        return Confidence.MEDIUM
    return Confidence.LOW


def requires_human_review(status: Status, confidence: Confidence) -> bool:
    """Only a HIGH-confidence RESOLVED/UNRESOLVED is allowed to skip human review."""
    return not (status in (Status.RESOLVED, Status.UNRESOLVED) and confidence is Confidence.HIGH)


def combine_confidence(levels: list[Confidence]) -> Confidence:
    """A compound issue is only as trustworthy as its weakest part."""
    order = [Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]
    return min(levels, key=order.index) if levels else Confidence.LOW

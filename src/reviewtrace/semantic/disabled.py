"""The default semantic reviewer: off. No model is called and nothing leaves the machine."""

from __future__ import annotations

from reviewtrace.models.evidence import Evidence
from reviewtrace.models.finding import SemanticAssessment
from reviewtrace.models.issue import ReviewIssue
from reviewtrace.semantic.base import SemanticError, SemanticReviewer


class DisabledSemanticReviewer(SemanticReviewer):
    name = "disabled"
    enabled = False

    def assess(
        self,
        issue: ReviewIssue,
        original_context: str,
        revised_context: str,
        evidence: list[Evidence],
    ) -> SemanticAssessment:
        raise SemanticError("semantic assessment is disabled")

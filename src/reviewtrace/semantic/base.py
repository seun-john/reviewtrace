"""Optional semantic assessment.

ReviewTrace works without any language model. This module defines the interface a
semantic reviewer must implement, the structured payload it receives, the structured
output it must return, and the policy that decides how much of that output to trust.

Nothing here performs network I/O. A concrete adapter (Anthropic, OpenAI, Gemini, a local
model, an OpenAI-compatible endpoint) is a subclass of :class:`SemanticReviewer` that the
caller constructs and passes in explicitly; documents are never sent anywhere otherwise.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError

from reviewtrace.models.evidence import Evidence, EvidenceDirection
from reviewtrace.models.finding import (
    AssessmentSource,
    Confidence,
    Finding,
    SemanticAssessment,
    Status,
)
from reviewtrace.models.issue import ReviewIssue
from reviewtrace.utils.errors import ReviewTraceError


class SemanticError(ReviewTraceError):
    """A semantic reviewer failed or returned unusable output."""


class SemanticReviewer(ABC):
    """Interface for an explicitly enabled semantic reviewer."""

    name: str = "semantic"
    enabled: bool = True

    @abstractmethod
    def assess(
        self,
        issue: ReviewIssue,
        original_context: str,
        revised_context: str,
        evidence: list[Evidence],
    ) -> SemanticAssessment:
        """Return a structured assessment of whether ``issue`` was addressed."""


def build_payload(
    issue: ReviewIssue,
    original_context: str,
    revised_context: str,
    evidence: list[Evidence],
) -> dict[str, Any]:
    """The provider-independent request an adapter should render into its own prompt.

    A bare "was this resolved? yes/no" question is never asked: the reviewer sees the
    request, both versions of the relevant text and the detected changes, and must answer
    in the structure described by ``response_schema``.
    """
    return {
        "reviewer_request": issue.comment_text,
        "anchor_text": issue.anchor_text,
        "requested_action": issue.requested_action,
        "original_context": original_context,
        "revised_context": revised_context,
        "detected_changes": [
            {
                "index": i,
                "type": e.type.value,
                "explanation": e.explanation,
                "revised_text": e.revised_text,
            }
            for i, e in enumerate(evidence)
        ],
        "response_schema": SemanticAssessment.model_json_schema(),
        "instructions": (
            "Assess whether the revised text addresses the request. Answer with status_recommendation, "
            "explanation, evidence_refs (indices into detected_changes), missing_requirements and confidence. "
            "Use NEEDS_REVIEW when unsure."
        ),
    }


def parse_assessment(reviewer: str, raw: str | dict[str, Any]) -> SemanticAssessment:
    """Validate a reviewer's structured output; anything malformed is rejected."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
        data["reviewer"] = reviewer
        return SemanticAssessment.model_validate(data)
    except (ValueError, TypeError, ValidationError) as exc:
        raise SemanticError(f"{reviewer}: unusable semantic output ({exc})") from exc


def merge_assessment(finding: Finding, assessment: SemanticAssessment) -> Finding:
    """Combine a semantic assessment with the deterministic finding under strict rules.

    * A HIGH-confidence deterministic RESOLVED/UNRESOLVED is never overridden.
    * A semantic RESOLVED/PARTIALLY_RESOLVED/UNRESOLVED must cite evidence that exists;
      otherwise it is downgraded to NEEDS_REVIEW.
    * Semantic confidence is capped at MEDIUM and the result always requires human review.
    """
    if (
        finding.status in (Status.RESOLVED, Status.UNRESOLVED)
        and finding.confidence is Confidence.HIGH
    ):
        return finding.model_copy(update={"semantic": assessment})

    refs = [i for i in assessment.evidence_refs if 0 <= i < len(finding.evidence)]
    status = assessment.status_recommendation
    rationale = assessment.explanation
    if status in (Status.RESOLVED, Status.PARTIALLY_RESOLVED, Status.UNRESOLVED) and not refs:
        status = Status.NEEDS_REVIEW
        rationale += (
            " (Downgraded: the semantic reviewer cited no evidence from the detected changes.)"
        )
    if status is Status.ERROR:
        status = Status.NEEDS_REVIEW

    evidence = list(finding.evidence)
    if status is Status.RESOLVED:
        cited = [finding.evidence[i] for i in refs]
        evidence.extend(
            e.model_copy(
                update={
                    "direction": EvidenceDirection.SUPPORTS,
                    "explanation": f"cited by semantic reviewer: {e.explanation}",
                }
            )
            for e in cited
        )
    confidence = Confidence.LOW if assessment.confidence is Confidence.LOW else Confidence.MEDIUM
    if status is Status.RESOLVED and confidence is Confidence.LOW:
        status = Status.NEEDS_REVIEW
    return Finding(
        issue_id=finding.issue_id,
        status=status,
        confidence=confidence,
        rationale=rationale,
        evidence=evidence,
        missing_elements=assessment.missing_requirements or finding.missing_elements,
        remaining_action=finding.remaining_action,
        requires_human_review=True,
        assessment_source=AssessmentSource.SEMANTIC,
        method=f"semantic:{assessment.reviewer}",
        document_changed=finding.document_changed,
        word_comment_resolved=finding.word_comment_resolved,
        sub_findings=finding.sub_findings,
        semantic=assessment,
        deterministic_status=finding.status,
    )

"""Finding: ReviewTrace's assessment of one issue, with the evidence it rests on.

Three different things are kept apart on purpose:

* what the reviewer's own tool recorded      -> ``word_comment_resolved``
* what changed in the document (objective)   -> ``document_changed`` and ``evidence``
* whether the request was addressed          -> ``status`` (ReviewTrace's assessment)
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator

from reviewtrace.models.document import Location
from reviewtrace.models.evidence import Evidence, EvidenceDirection


class Status(str, Enum):
    RESOLVED = "RESOLVED"
    PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"
    ERROR = "ERROR"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")


STATUS_ORDER = [
    Status.RESOLVED,
    Status.PARTIALLY_RESOLVED,
    Status.UNRESOLVED,
    Status.NEEDS_REVIEW,
    Status.NOT_ASSESSABLE,
    Status.ERROR,
]


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class AssessmentSource(str, Enum):
    DETERMINISTIC = "deterministic"
    SEMANTIC = "semantic"


class SemanticAssessment(BaseModel):
    """Structured output required from an optional semantic reviewer."""

    reviewer: str
    status_recommendation: Status
    explanation: str
    evidence_refs: list[int] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW


class Finding(BaseModel):
    issue_id: str
    status: Status
    confidence: Confidence
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)
    missing_elements: list[str] = Field(default_factory=list)
    remaining_action: str | None = None
    requires_human_review: bool = True
    assessment_source: AssessmentSource = AssessmentSource.DETERMINISTIC
    method: str = ""
    located_by: str = ""
    document_changed: bool | None = None
    word_comment_resolved: bool | None = None
    sub_findings: list[Finding] = Field(default_factory=list)
    semantic: SemanticAssessment | None = None
    deterministic_status: Status | None = None

    @model_validator(mode="after")
    def _enforce_invariants(self) -> Finding:
        if self.status is Status.RESOLVED:
            if not any(e.direction is EvidenceDirection.SUPPORTS for e in self.evidence):
                raise ValueError("a RESOLVED finding must contain supporting evidence")
            if self.confidence is Confidence.LOW:
                raise ValueError("a RESOLVED finding cannot have LOW confidence")
        return self

    @property
    def supporting_evidence(self) -> list[Evidence]:
        return [e for e in self.evidence if e.direction is EvidenceDirection.SUPPORTS]

    @property
    def revised_locations(self) -> list[Location]:
        out: list[Location] = []
        for e in self.supporting_evidence or self.evidence:
            loc = e.revised_location
            if loc is not None and loc not in out:
                out.append(loc)
        return out


Finding.model_rebuild()

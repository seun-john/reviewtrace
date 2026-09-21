"""Evidence: an objective observation that bears on a review issue."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from reviewtrace.models.document import Location


class EvidenceType(str, Enum):
    TEXT_ADDED = "text_added"
    TEXT_REMOVED = "text_removed"
    TEXT_MODIFIED = "text_modified"
    SECTION_ADDED = "section_added"
    SECTION_REMOVED = "section_removed"
    TABLE_CHANGED = "table_changed"
    REFERENCE_ADDED = "reference_added"
    FIGURE_CHANGED = "figure_changed"
    HEADING_CHANGED = "heading_changed"
    RELOCATION = "relocation"
    EXACT_MATCH = "exact_match"
    TRACKED_REVISION = "tracked_revision"
    NO_CHANGE = "no_change"
    CONTEXTUAL_MATCH = "contextual_match"


class EvidenceDirection(str, Enum):
    """How an observation bears on the request."""

    SUPPORTS = "supports"  # points toward the request having been addressed
    CONTRADICTS = "contradicts"  # points toward it NOT having been addressed
    CONTEXT = "context"  # relevant, but neither for nor against


class Evidence(BaseModel):
    type: EvidenceType
    direction: EvidenceDirection = EvidenceDirection.CONTEXT
    original_location: Location | None = None
    revised_location: Location | None = None
    original_text: str | None = None
    revised_text: str | None = None
    similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    explanation: str

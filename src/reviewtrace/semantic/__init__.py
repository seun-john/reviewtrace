"""Optional semantic assessment (disabled by default)."""

from reviewtrace.semantic.base import (
    SemanticError,
    SemanticReviewer,
    build_payload,
    merge_assessment,
    parse_assessment,
)
from reviewtrace.semantic.disabled import DisabledSemanticReviewer

__all__ = [
    "DisabledSemanticReviewer",
    "SemanticError",
    "SemanticReviewer",
    "build_payload",
    "merge_assessment",
    "parse_assessment",
]

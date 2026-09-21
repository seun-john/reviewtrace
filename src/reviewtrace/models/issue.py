"""ReviewIssue: one actionable piece of reviewer feedback, with its provenance."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from reviewtrace.models.document import CommentReply, Location
from reviewtrace.models.finding import Status


class IssueSource(str, Enum):
    WORD_COMMENT = "word_comment"
    REVIEW_REPORT = "review_report"
    ISSUES_FILE = "issues_file"


class IssueCategory(str, Enum):
    ADD = "ADD"
    EXPAND = "EXPAND"
    EXPLAIN = "EXPLAIN"
    CLARIFY = "CLARIFY"
    CORRECT = "CORRECT"
    REMOVE = "REMOVE"
    REPLACE = "REPLACE"
    UPDATE = "UPDATE"
    CITE = "CITE"
    REFERENCE = "REFERENCE"
    REFORMAT = "REFORMAT"
    RESTRUCTURE = "RESTRUCTURE"
    MOVE = "MOVE"
    MERGE = "MERGE"
    SPLIT = "SPLIT"
    VERIFY = "VERIFY"
    CONSISTENCY = "CONSISTENCY"
    TABLE = "TABLE"
    FIGURE = "FIGURE"
    STATISTICAL = "STATISTICAL"
    OTHER = "OTHER"


class Severity(str, Enum):
    MAJOR = "major"
    MINOR = "minor"
    SUGGESTION = "suggestion"


class SubRequirement(BaseModel):
    """One separable requirement inside a compound comment (``RT-014.1``)."""

    id: str
    text: str
    category: IssueCategory = IssueCategory.OTHER


class ReviewIssue(BaseModel):
    id: str
    source: IssueSource
    source_id: str | None = None
    reviewer: str = ""
    date: str | None = None
    comment_text: str
    anchor_text: str = ""
    anchor_location: Location = Field(default_factory=Location)
    surrounding_context: str = ""
    category: IssueCategory = IssueCategory.OTHER
    requested_action: str = ""
    severity: Severity | None = None
    # Assigned by an audit; None until the issue has been assessed.
    status: Status | None = None
    # What the reviewer's own tool recorded. This is NOT ReviewTrace's assessment.
    word_comment_resolved: bool | None = None
    thread: list[CommentReply] = Field(default_factory=list)
    sub_requirements: list[SubRequirement] = Field(default_factory=list)
    possible_duplicates: list[str] = Field(default_factory=list)
    actionable: bool = True
    subjective: bool = False
    hints: list[str] = Field(default_factory=list)
    truncated: bool = False

    @property
    def is_compound(self) -> bool:
        return len(self.sub_requirements) > 1

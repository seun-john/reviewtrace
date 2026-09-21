"""Exception types raised by ReviewTrace. The CLI turns these into clean messages."""

from __future__ import annotations


class ReviewTraceError(Exception):
    """Base class for every expected, user-facing failure."""


class UnsupportedFileTypeError(ReviewTraceError):
    """The input file extension or format is not supported."""


class DocumentReadError(ReviewTraceError):
    """A document could not be opened or parsed."""


class IssueFileError(ReviewTraceError):
    """An issues file (YAML) is missing, malformed or invalid."""

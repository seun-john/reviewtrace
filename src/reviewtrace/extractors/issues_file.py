"""Read and write the human-editable ``issues.yml`` format (safe YAML only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from reviewtrace.extractors.issues import build_issue, issue_id, mark_duplicates
from reviewtrace.models.document import Location
from reviewtrace.models.issue import IssueCategory, IssueSource, ReviewIssue, Severity
from reviewtrace.utils.errors import IssueFileError

FORMAT_VERSION = 1
MAX_BYTES = 5 * 1024 * 1024
MAX_ISSUES = 5000


class _Location(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: str | None = None
    paragraph: int | None = Field(default=None, ge=1)
    paragraph_end: int | None = Field(default=None, ge=1)
    table: int | None = Field(default=None, ge=1)


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    reviewer: str = ""
    date: str | None = None
    comment: str
    anchor: str = ""
    context: str = ""
    location: _Location = Field(default_factory=_Location)
    category: IssueCategory | None = None
    severity: Severity | None = None
    source_id: str | None = None
    parts: list[str] = Field(default_factory=list)
    word_comment_resolved: bool | None = None


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    issues: list[_Entry]


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    if len(data) > 70:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=">")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_representer)


def load_issues_file(path: str | Path) -> list[ReviewIssue]:
    p = Path(path)
    if not p.exists():
        raise IssueFileError(f"issues file not found: {p}")
    if not p.is_file():
        raise IssueFileError(f"not a file: {p}")
    if p.suffix.lower() not in (".yml", ".yaml"):
        raise IssueFileError(f"{p.name}: an issues file must be .yml or .yaml")
    if p.stat().st_size > MAX_BYTES:
        raise IssueFileError(f"{p.name}: file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    try:
        data: Any = yaml.safe_load(p.read_bytes().decode("utf-8-sig"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise IssueFileError(f"{p.name}: invalid YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise IssueFileError(f"{p.name}: expected a mapping with 'version' and 'issues'")
    try:
        parsed = _File.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
        )
        raise IssueFileError(f"{p.name}: {details}") from exc
    if parsed.version != FORMAT_VERSION:
        raise IssueFileError(
            f"{p.name}: unsupported version {parsed.version} (expected {FORMAT_VERSION})"
        )
    if len(parsed.issues) > MAX_ISSUES:
        raise IssueFileError(f"{p.name}: more than {MAX_ISSUES} issues")

    issues: list[ReviewIssue] = []
    seen: set[str] = set()
    for n, e in enumerate(parsed.issues, start=1):
        iid = (e.id or issue_id(n)).strip()
        if iid in seen:
            raise IssueFileError(f"{p.name}: duplicate issue id '{iid}'")
        seen.add(iid)
        if not e.comment.strip():
            raise IssueFileError(f"{p.name}: issue '{iid}' has an empty comment")
        loc = Location(
            section=e.location.section,
            paragraph_start=e.location.paragraph,
            paragraph_end=e.location.paragraph_end,
            table_index=e.location.table - 1 if e.location.table else None,
        )
        issues.append(
            build_issue(
                iid,
                IssueSource.ISSUES_FILE,
                e.comment,
                source_id=e.source_id,
                reviewer=e.reviewer,
                date=e.date,
                anchor_text=e.anchor,
                anchor_location=loc,
                surrounding_context=e.context,
                severity=e.severity,
                category=e.category,
                parts=e.parts or None,
                word_comment_resolved=e.word_comment_resolved,
            )
        )
    return mark_duplicates(issues)


def issues_to_yaml(issues: list[ReviewIssue]) -> str:
    entries: list[dict[str, Any]] = []
    for i in issues:
        entry: dict[str, Any] = {"id": i.id}
        if i.reviewer:
            entry["reviewer"] = i.reviewer
        if i.date:
            entry["date"] = i.date
        entry["comment"] = i.comment_text
        if i.anchor_text:
            entry["anchor"] = i.anchor_text
        loc: dict[str, Any] = {}
        if i.anchor_location.section:
            loc["section"] = i.anchor_location.section
        if i.anchor_location.paragraph_start:
            loc["paragraph"] = i.anchor_location.paragraph_start
        if (
            i.anchor_location.paragraph_end
            and i.anchor_location.paragraph_end != i.anchor_location.paragraph_start
        ):
            loc["paragraph_end"] = i.anchor_location.paragraph_end
        if i.anchor_location.table_index is not None:
            loc["table"] = i.anchor_location.table_index + 1
        if loc:
            entry["location"] = loc
        entry["category"] = i.category.value
        if i.severity:
            entry["severity"] = i.severity.value
        if i.source_id:
            entry["source_id"] = i.source_id
        if len(i.sub_requirements) > 1:
            entry["parts"] = [s.text for s in i.sub_requirements]
        if i.word_comment_resolved is not None:
            entry["word_comment_resolved"] = i.word_comment_resolved
        entries.append(entry)
    return yaml.dump(
        {"version": FORMAT_VERSION, "issues": entries},
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )

"""JSON serialisation of an audit (also the input format for ``response`` and ``matrix``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from reviewtrace.models.report import SCHEMA_VERSION, AuditReport
from reviewtrace.reports.matrix import build_matrix
from reviewtrace.utils.errors import ReviewTraceError

MAX_REPORT_BYTES = 100 * 1024 * 1024


def render_json(report: AuditReport, indent: int = 2) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=indent, ensure_ascii=False) + "\n"


def render_matrix_json(report: AuditReport, indent: int = 2) -> str:
    rows = [r.as_dict() for r in build_matrix(report)]
    return json.dumps(rows, indent=indent, ensure_ascii=False) + "\n"


def report_from_dict(data: Any) -> AuditReport:
    if not isinstance(data, dict):
        raise ReviewTraceError("audit data must be a JSON object")
    version = data.get("schema_version")
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ReviewTraceError(f"unsupported audit schema version: {version!r}")
    try:
        return AuditReport.model_validate(data)
    except ValidationError as exc:
        raise ReviewTraceError(f"invalid audit data: {exc.errors()[0]['msg']}") from exc


def load_report(path: str | Path) -> AuditReport:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise ReviewTraceError(f"audit file not found: {p}")
    if p.stat().st_size > MAX_REPORT_BYTES:
        raise ReviewTraceError(f"{p.name}: audit file is too large")
    try:
        data = json.loads(p.read_bytes().decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReviewTraceError(f"{p.name}: not valid JSON ({exc})") from exc
    return report_from_dict(data)

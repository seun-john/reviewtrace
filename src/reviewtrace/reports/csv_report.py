"""CSV traceability matrix.

Reviewer comments are untrusted text. Spreadsheet applications treat cells that start with
``= + - @`` as formulas, so such cells are prefixed with an apostrophe.
"""

from __future__ import annotations

import csv
import io

from reviewtrace.models.report import AuditReport
from reviewtrace.reports.matrix import CORE_COLUMNS, EXTRA_COLUMNS, build_matrix

_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: str) -> str:
    return "'" + value if value.startswith(_FORMULA_STARTS) else value


def render_csv(report: AuditReport) -> str:
    columns = CORE_COLUMNS + EXTRA_COLUMNS
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([title for title, _ in columns])
    for row in build_matrix(report):
        data = row.as_dict()
        writer.writerow([safe_cell(data[key]) for _, key in columns])
    return buf.getvalue()

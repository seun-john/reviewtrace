"""File helpers: hashing (to show source files were not modified) and path confinement."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from reviewtrace.utils.errors import ReviewTraceError

ROOT_ENV = "REVIEWTRACE_MCP_ROOT"


def sha256_of(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def confine(path: str | Path, root: str | Path | None = None) -> Path:
    """Resolve ``path``; when a root is configured, refuse anything outside it."""
    resolved = Path(path).expanduser().resolve()
    base = root if root is not None else os.environ.get(ROOT_ENV)
    if base:
        root_path = Path(base).expanduser().resolve()
        if root_path != resolved and root_path not in resolved.parents:
            raise ReviewTraceError(f"path is outside the permitted directory: {resolved}")
    return resolved

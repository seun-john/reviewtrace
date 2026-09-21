"""Regenerate the example DOCX/Markdown/YAML files under ``examples/``.

Run from the repository root:  python scripts/build_examples.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fixtures.demo import write_all  # noqa: E402

if __name__ == "__main__":
    written = write_all(ROOT / "examples")
    for name, path in written.items():
        print(f"{name:14} {path.relative_to(ROOT)}")

from __future__ import annotations

import pytest

from tests.fixtures import demo


@pytest.fixture()
def demo_files(tmp_path):  # type: ignore[no-untyped-def]
    return demo.write_all(tmp_path)

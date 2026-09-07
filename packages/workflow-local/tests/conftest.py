"""The `case` fixture: every scenario in `test_conformance.py` runs once per backend."""

from __future__ import annotations

import pytest
from conformance import CASES
from marks import ON_WINDOWS, POSIX_ONLY_REASON


@pytest.fixture(params=CASES, ids=lambda c: c.id)
def case(request):
    if request.param.posix_only and ON_WINDOWS:
        pytest.skip(f"{request.param.id}: {POSIX_ONLY_REASON}")
    return request.param


@pytest.fixture
def session(case, tmp_path):
    backend = case.make(tmp_path / "root")
    opened = backend.open_session("conformance")
    yield opened
    opened.close()

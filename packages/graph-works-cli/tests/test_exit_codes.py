"""Identity with `code_graph_io.exit_codes` — guards drift if one is edited without the other."""

from __future__ import annotations

import code_graph_io.exit_codes as _gio
from graph_works_cli import exit_codes

_NAMES = (
    "SUCCESS",
    "GENERIC",
    "STALE",
    "NOT_INITIALIZED",
    "SCHEMA_MISMATCH",
    "NOT_IN_GIT_REPO",
    "UPDATE_IN_PROGRESS",
    "AMBIGUOUS",
)


def test_identity_with_code_graph_io() -> None:
    for name in _NAMES:
        assert getattr(exit_codes, name) == getattr(_gio, name), name


def test_all_matches_the_full_name_set() -> None:
    assert set(exit_codes.__all__) == set(_NAMES)

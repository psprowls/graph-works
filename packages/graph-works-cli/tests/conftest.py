"""Shared fixtures for the `gw` CLI suite."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import initialized_workspace as _initialized_workspace


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A real, bootstrapped workspace — the smallest fixture that exercises the
    actual `--workspace` resolution and on-disk `.gw`/`okf` layout.

    `gw bootstrap` seeds `.gw/tags.yaml` with a starter vocabulary (`perf`,
    `security`) so a fresh workspace already has *something* to validate
    against. The `gw wiki tags` suite needs a workspace with no vocabulary at
    all to exercise the missing-file path, and every other test in that suite
    supplies its own vocabulary explicitly via `seed(..., vocabulary=...)` —
    so the starter file is removed here rather than left to shadow whichever
    vocabulary a test actually means to test against.
    """
    root = _initialized_workspace(tmp_path / "works")
    (root / ".gw" / "tags.yaml").unlink()
    return root

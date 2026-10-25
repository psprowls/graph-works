"""Shared fixtures for the `gw` CLI suite."""

from __future__ import annotations

from pathlib import Path

import pytest
from conformance_drivers import AttendedDriver, AutoDriveDriver, RunLog
from conformance_fixture import Fixture, Oracle, build_fixture
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


@pytest.fixture(scope="session")
def attended(tmp_path_factory: pytest.TempPathFactory) -> tuple[Fixture, RunLog]:
    """One attended run of the conformance epic, shared by every test that inspects it."""
    fx = build_fixture(tmp_path_factory.mktemp("attended"))
    with pytest.MonkeyPatch.context() as monkeypatch:
        return fx, AttendedDriver().run(fx, Oracle(fx), monkeypatch)


@pytest.fixture(scope="session")
def auto_driven(tmp_path_factory: pytest.TempPathFactory) -> tuple[Fixture, RunLog]:
    """One auto-drive run of the same epic, shared by every test that inspects it."""
    fx = build_fixture(tmp_path_factory.mktemp("auto"))
    with pytest.MonkeyPatch.context() as monkeypatch:
        return fx, AutoDriveDriver().run(fx, Oracle(fx), monkeypatch)

"""Shared fixtures for the `gw` CLI suite."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from conformance_drivers import AttendedDriver, AutoDriveDriver, RunLog
from conformance_fixture import Fixture, Oracle, build_fixture
from conformance_shared import shared_run
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


def _shared(tmp_path_factory: pytest.TempPathFactory, name: str, driver: Any) -> tuple[Fixture, RunLog]:
    """Run *driver* once per session: under xdist the first worker builds it, the rest read its pickle."""

    def build() -> tuple[Fixture, RunLog]:
        fx = build_fixture(tmp_path_factory.mktemp(name))
        with pytest.MonkeyPatch.context() as monkeypatch:
            return fx, driver().run(fx, Oracle(fx), monkeypatch)

    if not os.environ.get("PYTEST_XDIST_WORKER"):
        return build()
    return shared_run(name, tmp_path_factory.getbasetemp().parent / "conformance", build)  # type: ignore[no-any-return]


@pytest.fixture(scope="session")
def attended(tmp_path_factory: pytest.TempPathFactory) -> tuple[Fixture, RunLog]:
    """One attended run of the conformance epic, shared by every test that inspects it."""
    return _shared(tmp_path_factory, "attended", AttendedDriver)


@pytest.fixture(scope="session")
def auto_driven(tmp_path_factory: pytest.TempPathFactory) -> tuple[Fixture, RunLog]:
    """One auto-drive run of the same epic, shared by every test that inspects it."""
    return _shared(tmp_path_factory, "auto", AutoDriveDriver)

"""The package's own invariants: it imports, it is versioned, and it exports
what it says it exports."""

from __future__ import annotations

import tomllib
from pathlib import Path

import gw_kernel
import pytest
from gw_kernel.workspace.errors import InitError, WorkspaceError, WorkspaceNotFound


def test_version_is_static_and_matches_the_distribution():
    assert gw_kernel.__version__ == "0.7.0"


def test_private_descriptor_loader_has_a_synchronized_okf_io_floor():
    manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with manifest.open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]
    assert "okf-io>=0.2.6,<0.3" in dependencies


def test_all_is_sorted_and_every_name_is_bound():
    assert gw_kernel.__all__ == sorted(gw_kernel.__all__, key=_ruff_key)
    for name in gw_kernel.__all__:
        assert hasattr(gw_kernel, name), name


def _ruff_key(name: str) -> tuple[int, str]:
    """RUF022's grouping: SCREAMING_SNAKE constants, then CapWords, then the
    rest, alphabetical within each group."""
    if name.isupper():
        return (0, name)
    if name[:1].isupper():
        return (1, name)
    return (2, name)


def test_every_refusal_is_a_workspace_error_and_a_value_error():
    for error in (WorkspaceNotFound, InitError):
        assert issubclass(error, WorkspaceError)
        assert issubclass(error, ValueError)


def test_lint_all_retired_rather_than_ported():
    """47 lines that do not port: once `compose_lanes` returns the lanes and
    `run_mechanical` loops over them, a second orchestrator over that loop has
    nothing left to do. Its continue-on-error behaviour survives as the loop's
    own per-lane error capture."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("gw_kernel.commands.lint_all")


def test_the_dispatch_seam_is_reachable_from_the_front_door():
    assert hasattr(gw_kernel, "PackagedRule")
    for name in (
        "orchestrate_plan",
        "run_orchestrate",
        "run_stage_advance",
        "OrchestratePlan",
        "OrchestrateResult",
        "PlannedAdvance",
        "BlockedItem",
        "StageAdvance",
        "PlacementRecord",
        "ReaderRecord",
        "read_reader_receipt",
        "reader_receipt_path",
        "run_record_baseline",
        "run_record_placement",
        "run_record_reader",
    ):
        assert name not in gw_kernel.__all__
        assert not hasattr(gw_kernel, name)


def test_archive_exports_have_left_the_kernel():
    for name in ("ArchiveRun", "run_archive"):
        assert name not in gw_kernel.__all__
        assert not hasattr(gw_kernel, name)


def test_provenance_is_read_through_its_module():
    """`worktree_state` and `results_facts` stay module-qualified: three of the
    most generic names in the language do not belong at a package's front
    door, which is the rule `commands/__init__.py` already states."""
    for name in ("worktree_state", "results_facts", "write_active_work"):
        assert not hasattr(gw_kernel, name), name


def test_the_query_error_is_exported():
    from gw_kernel import QueryError
    from gw_kernel.workspace.errors import WorkspaceError

    assert issubclass(QueryError, WorkspaceError)
    assert "QueryError" in gw_kernel.__all__


def test_nonblocking_lock_dependency_floor():
    manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with manifest.open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]
    assert "okf-ext[schemas]>=0.5.5,<0.6" in dependencies

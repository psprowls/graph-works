"""The package imports, is typed, and declares exactly one dependency."""

from __future__ import annotations

import tomllib
from pathlib import Path

import workflow_orca

PKG_ROOT = Path(__file__).resolve().parents[1]


def _metadata() -> dict:
    return tomllib.loads((PKG_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_is_static():
    assert workflow_orca.__version__ == _metadata()["project"]["version"]


def test_ships_a_py_typed_marker():
    assert (PKG_ROOT / "src" / "workflow_orca" / "py.typed").is_file()


def test_declares_one_dependency_and_no_extras():
    # A dependency here is a scope decision. `subagents-io` is the protocol
    # this package implements; there is no second edge and no extra, because
    # the `orca` CLI is a process, not a library.
    meta = _metadata()
    assert meta["project"]["dependencies"] == ["subagents-io>=0.2,<0.3"]
    assert "optional-dependencies" not in meta["project"]


def test_all_is_sorted_and_bound():
    assert workflow_orca.__all__ == sorted(workflow_orca.__all__)
    for name in workflow_orca.__all__:
        assert hasattr(workflow_orca, name), name


def test_the_public_surface_is_exactly_four_names():
    # `OrcaResult` and `OrcaCliError` are surface because a caller injecting
    # its own `run=` cannot annotate the callable without the first, and
    # cannot catch this package's specific failure without the second.
    # `_map` stays private: it is translation, and a caller already has the
    # protocol's vocabulary.
    assert set(workflow_orca.__all__) == {"OrcaBackend", "OrcaCliError", "OrcaResult", "OrcaSession"}

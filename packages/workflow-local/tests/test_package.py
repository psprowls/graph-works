"""The package imports, is typed, and declares exactly one runtime dependency."""

from __future__ import annotations

import tomllib
from pathlib import Path

import workflow_local

PKG_ROOT = Path(__file__).resolve().parents[1]


def _metadata() -> dict:
    return tomllib.loads((PKG_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_is_static():
    assert workflow_local.__version__ == _metadata()["project"]["version"]


def test_ships_a_py_typed_marker():
    assert (PKG_ROOT / "src" / "workflow_local" / "py.typed").is_file()


def test_declares_its_one_runtime_dependency_and_no_extras():
    # Asserted by exact list rather than by membership: a dependency here is a
    # scope decision. The second assertion is a different rule and does not
    # move — a vendor SDK arriving as an extra would make this the package that
    # runs two backends, and the naming rule is one package per backend.
    meta = _metadata()
    assert meta["project"]["dependencies"] == ["subagents-io>=0.2,<0.3"]
    assert "optional-dependencies" not in meta["project"]


def test_all_is_sorted_and_bound():
    assert workflow_local.__all__ == sorted(workflow_local.__all__)
    for name in workflow_local.__all__:
        assert hasattr(workflow_local, name), name

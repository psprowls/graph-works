"""Working checkout declarations in the layered workspace manifest."""

from __future__ import annotations

from pathlib import Path

import pytest
from graph_works_core.workspace.config import declared_checkouts, load_workspace_config
from graph_works_core.workspace.errors import WorkspaceConfigError
from graph_works_core.workspace.layout import WorkspaceLayout, layout_for
from graph_works_core.workspace.manifest import set_value


def _layout(tmp_path: Path, repositories: str) -> WorkspaceLayout:
    (tmp_path / "workspace.yaml").write_text(f"version: 1\n{repositories}", encoding="utf-8", newline="")
    return layout_for(tmp_path)


def test_a_relative_checkout_resolves_against_the_manifest_directory(tmp_path: Path) -> None:
    layout = _layout(
        tmp_path,
        "repositories:\n  demo:\n    path: okf/repositories/demo/references/git\n"
        "    checkout: .gw/worktrees/demo/main\n",
    )
    assert declared_checkouts(layout) == {"demo": (tmp_path / ".gw/worktrees/demo/main").resolve()}


def test_a_climbing_checkout_resolves_outside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    layout = _layout(root, "repositories:\n  demo:\n    path: x\n    checkout: ../elsewhere/demo\n")
    assert declared_checkouts(layout) == {"demo": (tmp_path / "elsewhere/demo").resolve()}


def test_workspace_local_yaml_overrides_the_checkout(tmp_path: Path) -> None:
    layout = _layout(tmp_path, "repositories:\n  demo:\n    path: x\n    checkout: .gw/worktrees/demo/main\n")
    machine = tmp_path / "machine" / "demo"
    (tmp_path / "workspace.local.yaml").write_text(
        f"repositories:\n  demo:\n    checkout: {machine}\n", encoding="utf-8", newline=""
    )
    assert declared_checkouts(layout) == {"demo": machine}


def test_entries_without_checkout_are_absent_and_no_manifest_is_empty(tmp_path: Path) -> None:
    layout = _layout(tmp_path, "repositories:\n  plain:\n    path: code\n")
    assert declared_checkouts(layout) == {}
    assert declared_checkouts(layout_for(tmp_path / "nowhere")) == {}


def test_the_workspace_config_still_refuses_an_unknown_key(tmp_path: Path) -> None:
    layout = _layout(tmp_path, "repositories:\n  demo:\n    path: x\n    chekout: y\n")
    with pytest.raises(WorkspaceConfigError, match="chekout"):
        load_workspace_config(layout)


def test_set_value_accepts_the_checkout_key(tmp_path: Path) -> None:
    layout = _layout(tmp_path, "repositories: {}\n")
    set_value(layout.manifest_path, "repositories.demo.path", "okf/repositories/demo/references/git")
    set_value(layout.manifest_path, "repositories.demo.checkout", ".gw/worktrees/demo/main")
    assert declared_checkouts(layout) == {"demo": (tmp_path / ".gw/worktrees/demo/main").resolve()}

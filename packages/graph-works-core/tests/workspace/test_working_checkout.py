"""In-bundle repository clones resolve to their working checkouts."""

from __future__ import annotations

from pathlib import Path

import pytest
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import layout_for
from graph_works_core.workspace.repos import (
    declared_clone,
    declared_repositories,
    in_bundle_clone,
    resolve_item_repo,
    resolve_repo,
    resolve_repos,
)

CLONE = "okf/repositories/demo/references/git"


def _layout(tmp_path: Path, repositories: str):
    (tmp_path / "workspace.yaml").write_text(f"version: 1\nrepositories:\n{repositories}", encoding="utf-8", newline="")
    return layout_for(tmp_path)


def test_in_bundle_clone_is_the_exact_lane_location(tmp_path: Path) -> None:
    layout = _layout(tmp_path, "  x:\n    path: code\n")
    bundle = layout.bundle_dir
    assert in_bundle_clone(layout, bundle / "repositories/demo/references/git")
    assert in_bundle_clone(layout, bundle / "repositories/demo/references/git/.")
    assert not in_bundle_clone(layout, bundle / "repositories/demo/references/git/src")
    assert not in_bundle_clone(layout, bundle / "repositories/demo/references")
    assert not in_bundle_clone(layout, bundle / "repositories/a/b/references/git")
    assert not in_bundle_clone(layout, tmp_path / "code")


def test_an_in_bundle_path_with_a_checkout_resolves_to_the_checkout(tmp_path: Path) -> None:
    layout = _layout(tmp_path, f"  demo:\n    path: {CLONE}\n    checkout: .gw/worktrees/demo/main\n")
    checkout = (tmp_path / ".gw/worktrees/demo/main").resolve()
    assert declared_repositories(layout) == {"demo": checkout}
    assert resolve_repo(layout) == (checkout, None)
    assert resolve_repo(layout, repo_name="demo") == (checkout, None)
    assert resolve_repos(layout) == (checkout,)
    assert resolve_item_repo(layout, None, {}).path == checkout
    assert declared_clone(layout, "demo") == (tmp_path / CLONE).resolve()
    assert declared_clone(layout, "nope") is None


def test_an_in_bundle_path_without_a_checkout_refuses_by_name(tmp_path: Path) -> None:
    layout = _layout(tmp_path, f"  demo:\n    path: {CLONE}\n")
    message = (
        r"repositories\.demo is an in-bundle clone and declares no checkout; "
        r"add checkout: \(e\.g\. \.gw/worktrees/demo/<track>\)"
    )
    for call in (lambda: declared_repositories(layout), lambda: resolve_repo(layout), lambda: resolve_repos(layout)):
        with pytest.raises(WorkspaceError, match=message):
            call()


def test_an_ordinary_path_is_unchanged_and_ignores_a_stray_checkout(tmp_path: Path) -> None:
    layout = _layout(tmp_path, "  code:\n    path: code\n    checkout: elsewhere\n")
    assert declared_repositories(layout) == {"code": (tmp_path / "code").resolve()}


def test_a_workspace_local_override_wins(tmp_path: Path) -> None:
    layout = _layout(tmp_path, f"  demo:\n    path: {CLONE}\n    checkout: .gw/worktrees/demo/main\n")
    machine = tmp_path / "m" / "demo"
    (tmp_path / "workspace.local.yaml").write_text(
        f"repositories:\n  demo:\n    checkout: {machine}\n", encoding="utf-8", newline=""
    )
    assert declared_repositories(layout) == {"demo": machine}

"""Workspace commits can include explicitly named workspace-root members."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.commits import WorkspaceCommit
from graph_works_core.workspace.transactions import commit_pending


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def layout(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    layout = apply_init(plan_init(root, today=date(2026, 9, 29), topic="T")).layout
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return layout


def test_root_paths_commit_the_manifest_with_bundle_members(layout) -> None:
    layout.manifest_path.write_text(
        layout.manifest_path.read_text(encoding="utf-8") + "# x\n", encoding="utf-8", newline=""
    )
    (layout.bundle_dir / "log.md").write_text("changed\n", encoding="utf-8", newline="")
    outcome = commit_pending(
        layout, WorkspaceCommit("workspace: both", extra_paths=("log.md",), root_paths=("workspace.yaml",))
    )
    assert outcome.status == "committed"
    assert sorted(_git(layout.root, "show", "--name-only", "--format=", "HEAD").splitlines()) == [
        f"{layout.bundle_dir.relative_to(layout.root).as_posix()}/log.md",
        "workspace.yaml",
    ]


@pytest.mark.parametrize("bad", ["/etc/passwd", "../outside", "", "."])
def test_a_root_path_outside_the_workspace_fails_the_commit(layout, bad: str) -> None:
    outcome = commit_pending(layout, WorkspaceCommit("workspace: bad", root_paths=(bad,)))
    assert outcome.status == "failed" and "outside the workspace" in (outcome.reason or "")


def test_root_path_symlink_outside_git_top_level_fails_the_commit(layout, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8", newline="")
    (layout.root / "outside-link").symlink_to(outside)
    outcome = commit_pending(layout, WorkspaceCommit("workspace: bad", root_paths=("outside-link",)))
    assert outcome.status == "failed" and "outside the workspace" in (outcome.reason or "")

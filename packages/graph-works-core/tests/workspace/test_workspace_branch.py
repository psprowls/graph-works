"""The workspace's own repository as a placement target."""

from __future__ import annotations

import subprocess
from datetime import date

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.errors import WorkspaceConfigError
from graph_works_core.workspace.workspace_branch import WORKSPACE_REPO, workspace_repo


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _layout(tmp_path, *, git=True):
    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 26), topic="WS")).layout
    if git:
        _git(layout.root, "init", "-b", "main")
    return layout


def test_own_toplevel_repo_is_enabled(tmp_path):
    layout = _layout(tmp_path)
    repo, note = workspace_repo(layout)
    assert note is None
    assert repo is not None
    assert (repo.name, repo.path, repo.source) == (WORKSPACE_REPO, layout.root.resolve(), "workspace")


def test_embedded_workspace_is_disabled_with_a_note(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(outer, "init", "-b", "main")
    layout = apply_init(plan_init(outer / "ws", today=date(2026, 9, 26), topic="WS")).layout
    repo, note = workspace_repo(layout)
    assert repo is None
    assert note and "toplevel" in note


def test_no_git_is_disabled_with_a_note(tmp_path):
    repo, note = workspace_repo(_layout(tmp_path, git=False))
    assert repo is None and note


def test_commits_off_disables(tmp_path):
    layout = _layout(tmp_path)
    layout.manifest_path.write_text(
        layout.manifest_path.read_text(encoding="utf-8").replace(
            "workflow:\n", "workflow:\n  workspace_commits: off\n", 1
        ),
        encoding="utf-8",
        newline="\n",
    )
    repo, note = workspace_repo(layout)
    assert repo is None
    assert note and "workspace_commits" in note


def test_commits_on_does_not_enable_embedded_workspace(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(outer, "init", "-b", "main")
    layout = apply_init(plan_init(outer / "ws", today=date(2026, 9, 26), topic="WS")).layout
    layout.manifest_path.write_text(
        layout.manifest_path.read_text(encoding="utf-8").replace(
            "workflow:\n", "workflow:\n  workspace_commits: on\n", 1
        ),
        encoding="utf-8",
        newline="\n",
    )
    repo, note = workspace_repo(layout)
    assert repo is None
    assert note and "toplevel" in note


def test_manifest_refuses_the_reserved_name(tmp_path):
    layout = _layout(tmp_path)
    layout.manifest_path.write_text(
        f"version: 1\nrepositories:\n  _workspace: {{path: {tmp_path}}}\n", encoding="utf-8", newline="\n"
    )
    with pytest.raises(WorkspaceConfigError, match="_workspace"):
        load_workspace_config(layout)

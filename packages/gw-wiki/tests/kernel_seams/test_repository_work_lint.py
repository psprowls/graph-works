"""Repository lane facts flow into wiki and work lint."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from gitrepo import Upstream, git, make_upstream
from gw_kernel.workspace.config import load_workspace_config
from gw_kernel.workspace.lane_facts import repository_notes
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.repos import resolve_repos
from gw_wiki.lint_drift.lint import run_mechanical
from gw_wiki.repositories.commands import run_repo_add
from gw_work.commands import run_lint as run_work_lint
from workspace_fixture import NOW, make_workspace

TODAY = date(2026, 9, 30)


@pytest.fixture
def upstream(tmp_path: Path) -> Upstream:
    (tmp_path / "up").mkdir()
    up = make_upstream(tmp_path / "up")
    up.commit({"README.md": "# demo\n"}, "c1")
    return up


@pytest.fixture
def layout(tmp_path: Path, upstream: Upstream) -> WorkspaceLayout:
    layout = make_workspace(tmp_path)
    assert run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW).ok
    return layout


def _wiki_codes(layout: WorkspaceLayout) -> set[str]:
    report = run_mechanical(layout, load_workspace_config(layout), today=TODAY, repo_roots=resolve_repos(layout))
    return {f.code for lane in report.mechanical for f in lane.report.findings if f.code.startswith("repository.")}


def _ahead(layout: WorkspaceLayout) -> None:
    git(layout.worktrees_dir / "demo" / "main", "commit", "-q", "--allow-empty", "-m", "merged")


def test_behind_track_reaches_wiki_lint_work_lint_and_next_notes(layout: WorkspaceLayout) -> None:
    _ahead(layout)
    assert _wiki_codes(layout) == {"repository.behind-track"}
    work = run_work_lint(layout, load_workspace_config(layout), repo_roots=resolve_repos(layout), today=TODAY)
    assert [f.code for f in work.findings if f.code.startswith("repository.")] == ["repository.behind-track"]
    (note,) = repository_notes(layout, "demo")
    assert "demo" in note and "1 commit(s) ahead" in note and "gw repo advance demo" in note
    assert repository_notes(layout, "other") == ()

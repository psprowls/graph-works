"""Repository lane facts flow into wiki and work lint."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from gitrepo import Upstream, git, make_upstream
from graph_works_core.lint_drift.lint import run_mechanical
from graph_works_core.repositories.commands import run_repo_add
from graph_works_core.work.commands import run_lint as run_work_lint
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.lane_facts import repository_notes
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repos
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


def test_a_fresh_managed_repository_lints_clean(layout: WorkspaceLayout) -> None:
    assert _wiki_codes(layout) == set()
    assert repository_notes(layout, "demo") == ()


def test_behind_track_reaches_wiki_lint_work_lint_and_next_notes(layout: WorkspaceLayout) -> None:
    _ahead(layout)
    assert _wiki_codes(layout) == {"repository.behind-track"}
    work = run_work_lint(layout, load_workspace_config(layout), repo_roots=resolve_repos(layout), today=TODAY)
    assert [f.code for f in work.findings if f.code.startswith("repository.")] == ["repository.behind-track"]
    (note,) = repository_notes(layout, "demo")
    assert "demo" in note and "1 commit(s) ahead" in note and "gw repo advance demo" in note
    assert repository_notes(layout, "other") == ()


def test_a_dirty_clone_and_a_foreign_checkout_are_wiki_errors(layout: WorkspaceLayout) -> None:
    clone = layout.bundle_dir / "repositories" / "demo" / "references" / "git"
    (clone / "scratch.txt").write_text("x\n", encoding="utf-8", newline="")
    git(layout.worktrees_dir / "demo" / "main", "checkout", "-q", "-b", "feature")
    assert _wiki_codes(layout) == {"repository.dirty", "repository.checkout-invalid"}


def test_git_unavailable_is_one_lane_error_only_when_the_lane_has_pages(
    layout: WorkspaceLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "empty").mkdir()
    empty = make_workspace(tmp_path / "empty")
    monkeypatch.setenv("GW_GIT", str(tmp_path / "no-git"))
    monkeypatch.setenv("PATH", "")
    report = run_mechanical(layout, load_workspace_config(layout), today=TODAY)
    assert len([error for error in report.errors if "toolchain.git" in error]) == 1
    quiet = run_mechanical(empty, load_workspace_config(empty), today=TODAY)
    assert [error for error in quiet.errors if "toolchain.git" in error] == []

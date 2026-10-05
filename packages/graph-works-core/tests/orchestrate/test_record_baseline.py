"""`gw work record-baseline`: the attended execute stage's starting commit."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from graph_works_core.orchestrate.placement import run_record_baseline
from graph_works_core.work import commands as work
from graph_works_core.workspace.provenance import GIT_ENV
from test_orchestrate_shell import TODAY, _code_repo, _initialized_workspace, _ready, _write

PATH = "work/feature-a"
EPIC = "work/epic-x"
CHILD = f"{EPIC}/children/feature-c"


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _worktree(repo: Path, where: Path, branch: str, start: str) -> Path:
    _git(repo, "worktree", "add", "-q", "-b", branch, str(where), start)
    return where


def _page_bytes(layout, path: str) -> bytes:
    return (layout.bundle_dir / f"{path}.md").read_bytes()


def _setup(tmp_path: Path):
    layout = _initialized_workspace(tmp_path)
    repo, fork = _code_repo(tmp_path / "c")
    kept = re.sub(r"(?m)^repositories:.*\n(?:[ \t]+.*\n)*", "", layout.manifest_path.read_text(encoding="utf-8"))
    layout.manifest_path.write_text(
        kept + f"repositories:\n  code:\n    path: {json.dumps(str(repo))}\n", encoding="utf-8", newline="\n"
    )
    return layout, repo, fork


def test_an_attended_execute_records_head_once(tmp_path: Path) -> None:
    layout, repo, fork = _setup(tmp_path)
    _ready(layout, PATH)
    _git(repo, "checkout", "-q", "-b", "feature/b", fork)
    first = run_record_baseline(layout, PATH, cwd=repo, today=TODAY, dry_run=False)
    assert first.plan.refusal is None and first.written and first.plan.after == fork
    (repo / "packages/a/x.py").write_text("three\n", encoding="utf-8", newline="\n")
    _git(repo, "commit", "-qam", "work")
    again = run_record_baseline(layout, PATH, cwd=repo, today=TODAY, dry_run=False)
    assert again.plan.refusal is None and not again.plan.changed and again.plan.after == first.plan.after


def test_a_cwd_outside_the_items_repository_refuses(tmp_path: Path) -> None:
    layout, _repo, _fork = _setup(tmp_path)
    other, _ = _code_repo(tmp_path / "other")
    _ready(layout, PATH)
    result = run_record_baseline(layout, PATH, cwd=other, today=TODAY, dry_run=False)
    assert result.plan.refusal == "outside-repository" and not result.written


def test_a_diverged_head_refuses_baseline_conflict(tmp_path: Path) -> None:
    layout, repo, _fork = _setup(tmp_path)
    _ready(layout, PATH, extra=f"start_sha: {_head(repo)}\n")
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    result = run_record_baseline(layout, PATH, cwd=repo, today=TODAY, dry_run=False)
    assert result.plan.refusal == "baseline-conflict"


def test_a_non_execute_item_refuses(tmp_path: Path) -> None:
    layout, repo, _fork = _setup(tmp_path)
    _ready(layout, PATH, phase="plan")
    assert run_record_baseline(layout, PATH, cwd=repo, today=TODAY, dry_run=False).plan.refusal == "not-execute"


def test_a_stamped_item_refuses_the_main_checkout_and_a_sibling_worktree(tmp_path: Path) -> None:
    layout, repo, fork = _setup(tmp_path)
    mine = _worktree(repo, tmp_path / "mine", "feature/mine", fork)
    sibling = _worktree(repo, tmp_path / "sibling", "feature/sib", fork)
    _ready(layout, PATH, extra=f"worktree: {mine.as_posix()}\nbranch: feature/mine\n")
    before = _page_bytes(layout, PATH)
    for wrong in (repo, sibling):
        result = run_record_baseline(layout, PATH, cwd=wrong, today=TODAY, dry_run=False)
        assert result.plan.refusal == "wrong-checkout" and not result.written
        assert str(mine.resolve()) in result.plan.detail
    assert _page_bytes(layout, PATH) == before


def test_the_recorded_checkout_its_subdirectory_and_a_symlink_alias_succeed(tmp_path: Path) -> None:
    layout, repo, fork = _setup(tmp_path)
    mine = _worktree(repo, tmp_path / "mine", "feature/mine", fork)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(mine, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this host")
    _ready(layout, PATH, extra=f"worktree: {mine.as_posix()}\nbranch: feature/mine\n")
    for cwd in (mine / "packages/a", alias):
        result = run_record_baseline(layout, PATH, cwd=cwd, today=TODAY)
        assert result.plan.refusal is None and result.plan.after == fork


def test_a_malformed_recorded_worktree_is_not_an_absent_constraint(tmp_path: Path) -> None:
    layout, repo, fork = _setup(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature/b", fork)
    _ready(layout, PATH, extra="worktree: [not, a, path]\n")
    assert run_record_baseline(layout, PATH, cwd=repo, today=TODAY).plan.refusal == "invalid-item"


def test_a_first_recording_past_the_branch_point_refuses_already_started(tmp_path: Path) -> None:
    layout, repo, fork = _setup(tmp_path)  # repo is on feature/a, one commit past main
    _ready(layout, PATH)
    before = _page_bytes(layout, PATH)
    result = run_record_baseline(layout, PATH, cwd=repo, today=TODAY, dry_run=False)
    assert result.plan.refusal == "already-started" and not result.written
    assert fork in result.plan.detail and _head(repo) in result.plan.detail
    assert _page_bytes(layout, PATH) == before


def test_a_detached_head_at_the_branch_point_records(tmp_path: Path) -> None:
    layout, repo, fork = _setup(tmp_path)
    _git(repo, "checkout", "-q", "--detach", fork)
    _ready(layout, PATH)
    assert run_record_baseline(layout, PATH, cwd=repo, today=TODAY).plan.after == fork


def test_a_tag_shadowing_the_base_branch_is_ignored(tmp_path: Path) -> None:
    layout, repo, fork = _setup(tmp_path)
    _git(repo, "tag", "main", "feature/a")  # a tag named like the base, pointing past the fork
    _git(repo, "checkout", "-q", "-b", "feature/b", fork)
    _ready(layout, PATH)
    result = run_record_baseline(layout, PATH, cwd=repo, today=TODAY)
    assert result.plan.refusal is None and result.plan.after == fork


def _epic_child(layout, repo: Path, tmp_path: Path, *, anchor_branch: str | None) -> Path:
    """An epic anchored on `epic/x` (one commit ahead of main) and a child forked from that anchor."""
    _git(repo, "checkout", "-q", "-b", "epic/x", "main")
    (repo / "packages/a/x.py").write_text("epic\n", encoding="utf-8", newline="\n")
    _git(repo, "commit", "-qam", "epic work")
    epic_tip = _git(repo, "rev-parse", "HEAD")
    child = _worktree(repo, tmp_path / "child", "feature/c", epic_tip)
    anchor = f"worktree: {repo.as_posix()}\nbranch: {anchor_branch}\n" if anchor_branch else ""
    _write(layout, EPIC, type="Epic", phase="execute", work_status="in-progress", extra=anchor)
    _write(
        layout,
        CHILD,
        type="Feature",
        phase="execute",
        work_status="in-progress",
        extra=f"worktree: {child.as_posix()}\nbranch: feature/c\n",
    )
    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    return child


def test_a_child_forked_from_its_epic_anchor_is_measured_against_the_anchor(tmp_path: Path) -> None:
    layout, repo, _fork = _setup(tmp_path)
    child = _epic_child(layout, repo, tmp_path, anchor_branch="epic/x")
    result = run_record_baseline(layout, CHILD, cwd=child, today=TODAY)
    assert result.plan.refusal is None and result.plan.after == _head(child)


def test_child_without_an_anchor_branch_refuses_git_unavailable(tmp_path: Path) -> None:
    layout, repo, _fork = _setup(tmp_path)
    child = _epic_child(layout, repo, tmp_path, anchor_branch=None)
    result = run_record_baseline(layout, CHILD, cwd=child, today=TODAY)
    assert result.plan.refusal == "git-unavailable" and EPIC in result.plan.detail


def test_an_unrelated_base_refuses_git_unavailable(tmp_path: Path) -> None:
    layout, repo, _fork = _setup(tmp_path)
    child = _epic_child(layout, repo, tmp_path, anchor_branch="island")
    _git(repo, "checkout", "-q", "--orphan", "island")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "unrelated")
    result = run_record_baseline(layout, CHILD, cwd=child, today=TODAY)
    assert result.plan.refusal == "git-unavailable" and "merge-base" in result.plan.detail


def test_unavailable_configured_git_refuses(tmp_path: Path) -> None:
    layout, repo, fork = _setup(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature/b", fork)
    _ready(layout, PATH)
    result = run_record_baseline(
        layout, PATH, cwd=repo, today=TODAY, environ={**os.environ, GIT_ENV: str(tmp_path / "no-git")}
    )
    assert result.plan.refusal == "git-unavailable"


def test_a_dispatch_captured_baseline_replays_from_its_own_checkout_only(tmp_path: Path) -> None:
    layout, repo, fork = _setup(tmp_path)
    mine = _worktree(repo, tmp_path / "mine", "feature/mine", fork)
    _ready(layout, PATH, extra=f"worktree: {mine.as_posix()}\nbranch: feature/mine\nstart_sha: {fork}\n")
    (mine / "packages/a/x.py").write_text("later\n", encoding="utf-8", newline="\n")
    _git(mine, "commit", "-qam", "later")
    kept = run_record_baseline(layout, PATH, cwd=mine, today=TODAY, dry_run=False)
    assert kept.plan.refusal is None and not kept.plan.changed and kept.plan.after == fork
    assert run_record_baseline(layout, PATH, cwd=repo, today=TODAY).plan.refusal == "wrong-checkout"

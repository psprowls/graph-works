"""Strict probes the execute baseline uses to bind itself to a checkout."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from graph_works_core.workspace import provenance
from graph_works_core.workspace.provenance import GitExecutable, GitFailure


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def git() -> GitExecutable:
    resolved = provenance.resolve_git(None, environ=os.environ)
    assert isinstance(resolved, GitExecutable)
    return resolved


def _repo(root: Path) -> Path:
    root.mkdir(parents=True)
    _run(root, "init", "-q", "-b", "main")
    _run(root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "one")
    (root / "sub").mkdir()
    return root


def test_toplevel_is_canonical_from_a_subdirectory_and_a_linked_worktree(tmp_path: Path, git: GitExecutable) -> None:
    repo = _repo(tmp_path / "repo")
    linked = tmp_path / "linked"
    _run(repo, "worktree", "add", "-q", "-b", "side", str(linked))
    assert provenance.strict_toplevel(repo / "sub", git=git) == repo.resolve()
    assert provenance.strict_toplevel(linked, git=git) == linked.resolve()


def test_toplevel_outside_any_repository_is_a_failure(tmp_path: Path, git: GitExecutable) -> None:
    bare = tmp_path / "plain"
    bare.mkdir()
    assert isinstance(provenance.strict_toplevel(bare, git=git), GitFailure)


def test_merge_base_answers_the_fork_and_fails_on_unrelated_history(tmp_path: Path, git: GitExecutable) -> None:
    repo = _repo(tmp_path / "repo")
    fork = _run(repo, "rev-parse", "HEAD")
    _run(repo, "checkout", "-q", "-b", "feature")
    _run(repo, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "two")
    head = _run(repo, "rev-parse", "HEAD")
    assert provenance.strict_merge_base(repo, fork, head, git=git) == fork
    _run(repo, "checkout", "-q", "--orphan", "island")
    _run(repo, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "x")
    island = _run(repo, "rev-parse", "HEAD")
    assert isinstance(provenance.strict_merge_base(repo, island, head, git=git), GitFailure)


def test_same_repository_spans_linked_worktrees_but_not_other_repositories(tmp_path: Path, git: GitExecutable) -> None:
    repo = _repo(tmp_path / "repo")
    other = _repo(tmp_path / "other")
    linked = tmp_path / "linked"
    _run(repo, "worktree", "add", "-q", "-b", "side", str(linked))
    assert provenance.strict_same_repository(linked, repo, git=git) is True
    assert provenance.strict_same_repository(other, repo, git=git) is False
    plain = tmp_path / "plain"
    plain.mkdir()
    assert isinstance(provenance.strict_same_repository(plain, repo, git=git), GitFailure)

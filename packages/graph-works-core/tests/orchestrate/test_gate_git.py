from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from graph_works_core.orchestrate import gate_git
from graph_works_core.workspace import provenance


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "T")
    git(path, "config", "commit.gpgsign", "false")
    (path / "a.txt").write_text("a", encoding="utf-8", newline="\n")
    git(path, "add", "a.txt")
    git(path, "commit", "-m", "one")
    return path


def test_clean_snapshot_carries_head_and_tree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r")
    snap = gate_git.snapshot(repo)
    assert snap.head == git(repo, "rev-parse", "HEAD")
    assert snap.tree == git(repo, "rev-parse", "HEAD^{tree}")
    assert snap.dirty == ()


def test_untracked_and_modified_files_are_dirty(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r")
    (repo / "new.txt").write_text("x", encoding="utf-8", newline="\n")
    assert gate_git.snapshot(repo).dirty == ("new.txt",)


def test_a_rename_reports_the_new_path_once(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r")
    git(repo, "mv", "a.txt", "b.txt")
    assert gate_git.snapshot(repo).dirty == ("b.txt",)


def test_a_linked_worktree_is_the_same_repository(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r")
    git(repo, "worktree", "add", "-b", "f", str(tmp_path / "wt"))
    other = make_repo(tmp_path / "other")
    assert gate_git.same_repository(tmp_path / "wt", repo)
    assert not gate_git.same_repository(other, repo)


def test_log_dir_is_inside_the_git_directory_and_never_dirties(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r")
    git(repo, "worktree", "add", "-b", "f", str(tmp_path / "wt"))
    log_dir = gate_git.gate_log_dir(tmp_path / "wt", "feature-a")
    log_dir.mkdir(parents=True)
    (log_dir / "x.log").write_text("x", encoding="utf-8", newline="\n")
    assert log_dir.is_absolute() and "gw-gate" in log_dir.parts and log_dir.name == "feature-a"
    assert gate_git.snapshot(tmp_path / "wt").dirty == ()


def test_git_failure_is_git_unavailable_not_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provenance, "probe_git", lambda *a, **k: provenance.GitOutcome(None, "", "missing"))
    with pytest.raises(gate_git.GitUnavailable, match="missing"):
        gate_git.snapshot(tmp_path, git=provenance.GitExecutable("git", "path", "git version x"))


def test_not_a_repository_is_git_unavailable(tmp_path: Path) -> None:
    with pytest.raises(gate_git.GitUnavailable):
        gate_git.snapshot(tmp_path)


def test_an_unresolvable_git_is_git_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provenance, "resolve_git", lambda *a, **k: provenance.GitFailure("missing", "no git"))
    with pytest.raises(gate_git.GitUnavailable, match="no git"):
        gate_git.snapshot(tmp_path)

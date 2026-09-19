"""Git-state classification for project-rooted agent configuration files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from graph_works_core.agent_config.git_state import SubprocessGit


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".gitignore").write_text(".claude/settings.local.json\nforced.json\n", encoding="utf-8", newline="\n")
    (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8", newline="\n")
    (tmp_path / "forced.json").write_text("{}", encoding="utf-8", newline="\n")
    (tmp_path / "loose.json").write_text("{}", encoding="utf-8", newline="\n")
    _git(tmp_path, "add", ".gitignore", ".claude/settings.json")
    _git(tmp_path, "add", "-f", "forced.json")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_committed_ignored_untracked_and_tracked_but_ignored(repo: Path) -> None:
    git = SubprocessGit()
    assert git.state(repo, ".claude/settings.json") == ("committed", None)
    assert git.state(repo, ".claude/settings.local.json") == ("ignored", None)
    assert git.state(repo, "loose.json") == ("untracked", None)
    # A tracked entry must win before git checks a matching ignore rule.
    assert git.state(repo, "forced.json") == ("committed", None)
    assert git.state(repo, ".pi/settings.json") == ("untracked", None)


def test_non_repository_and_missing_git_are_unknown_with_a_cause(tmp_path: Path) -> None:
    assert SubprocessGit().state(tmp_path, "x.json") == ("unknown", "not-a-repository")
    assert SubprocessGit("git-definitely-not-installed").state(tmp_path, "x.json") == (
        "unknown",
        "git-missing",
    )


def test_an_unexpected_ls_files_exit_is_a_git_error(tmp_path: Path, monkeypatch) -> None:
    from graph_works_core.workspace.provenance import GitOutcome

    outcomes = iter(
        (
            GitOutcome(0, "true\n", "ok"),
            GitOutcome(2, "", "ok"),
        )
    )
    monkeypatch.setattr(SubprocessGit, "_run", lambda *args: next(outcomes))
    assert SubprocessGit().state(tmp_path, "x.json") == ("unknown", "git-error")


@pytest.mark.parametrize("failure", ["unexpected", "timeout", "listing", "empty", "bad-path"])
def test_repository_identity_failures_do_not_claim_outside_git(tmp_path, monkeypatch, failure):
    from graph_works_core.workspace.provenance import GitOutcome

    outcomes = {
        "unexpected": [GitOutcome(128, "", "ok", "fatal: dubious ownership")],
        "timeout": [GitOutcome(0, str(tmp_path), "ok"), GitOutcome(None, "", "timeout")],
        "listing": [GitOutcome(0, str(tmp_path), "ok"), GitOutcome(1, "", "ok")],
        "empty": [GitOutcome(0, "", "ok"), GitOutcome(0, "worktree /repo\0", "ok")],
        "bad-path": [GitOutcome(0, "\0", "ok"), GitOutcome(0, "worktree /repo\0", "ok")],
    }
    pending = iter(outcomes[failure])
    monkeypatch.setattr(SubprocessGit, "_run", lambda *args: next(pending))
    assert SubprocessGit().context(tmp_path).error

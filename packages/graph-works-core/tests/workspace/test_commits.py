"""`workspace.commits`: pathspec-limited, single-line, trailer-free workspace commits."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _transaction_helpers import _git, _init_git, _plan, _workspace
from graph_works_core.workspace import commits
from graph_works_core.workspace.commits import CommitOutcome, WorkspaceCommit, commit_workspace, item_stem, plan_paths
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import layout_for
from graph_works_core.workspace.provenance import GitOutcome
from okf_ext.moves import Move
from work_tracker_okf.mutation import PlannedWrite

ITEM = "work/feature-a"
SIBLING = "work/feature-b"


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def _set_mode(layout, value: str) -> None:
    layout.local_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    layout.local_manifest_path.write_text(f"workflow:\n  workspace_commits: {value}\n", encoding="utf-8", newline="\n")


def _own_repo(tmp_path: Path):
    layout = _workspace(tmp_path)
    _init_git(layout.root)
    return layout


def _log(root: Path) -> list[str]:
    return _git(root, "log", "--format=%s").splitlines()


def _files_in_head(root: Path) -> set[str]:
    return set(_git(root, "show", "--name-only", "--format=", "HEAD").split())


def _status(root: Path) -> str:
    return _git(root, "status", "--porcelain")


# -- value types -------------------------------------------------------------


@pytest.mark.parametrize(
    "subject", ["advance x", "workspace:advance", "workspace: ", "workspace: a\nb", "workspace: a\rb"]
)
def test_subject_validation_rejects(subject: str) -> None:
    with pytest.raises(ValueError):
        WorkspaceCommit(subject)


def test_item_stem_is_last_segment() -> None:
    assert item_stem("work/epic-x/children/feature-y") == "feature-y"


def test_plan_paths_unions_writes_moves_and_deletes(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    plan = _plan(
        layout,
        writes=(PlannedWrite("work/a.md", None, b"x"),),
        moves=(Move("work/b", "archive/b", is_asset=False),),
        deletes=("work/c.md",),
    )
    assert plan_paths(plan) == ("archive/b", "work/a.md", "work/b", "work/c.md")


# -- modes -------------------------------------------------------------------


def test_default_mode_is_auto(tmp_path: Path) -> None:
    assert commits.commit_mode(_workspace(tmp_path)) == "auto"


def test_invalid_mode_is_a_configuration_error(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _set_mode(layout, "sometimes")
    with pytest.raises(WorkspaceError, match="workspace_commits"):
        commits.commit_mode(layout)


def test_off_skips_silently(tmp_path: Path) -> None:
    layout = _own_repo(tmp_path)
    _set_mode(layout, "off")
    _write(layout.bundle_dir, "work/a.md", "a\n")
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a.md",))
    assert (outcome.status, outcome.reason) == ("skipped", "disabled")


def test_no_repo_skips_not_a_repo(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _write(layout.bundle_dir, "work/a.md", "a\n")
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a.md",))
    assert (outcome.status, outcome.reason) == ("skipped", "not-a-repo")


def test_auto_skips_embedded_workspace(tmp_path: Path) -> None:
    host = _init_git(tmp_path / "code")
    layout = layout_for(host / ".works")
    layout.bundle_dir.mkdir(parents=True)
    _write(layout.bundle_dir, "work/a.md", "a\n")
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a.md",))
    assert (outcome.status, outcome.reason) == ("skipped", "not-own-repo")
    assert _log(host) == ["workspace: seed"]


def test_on_mode_embedded_commits_relative_to_toplevel(tmp_path: Path) -> None:
    host = _init_git(tmp_path / "code")
    layout = layout_for(host / ".works")
    layout.bundle_dir.mkdir(parents=True)
    _set_mode(layout, "on")
    _write(layout.bundle_dir, "work/a.md", "a\n")
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a.md",))
    assert outcome.status == "committed", outcome
    rel = (layout.bundle_dir / "work/a.md").relative_to(host).as_posix()
    assert _files_in_head(host) == {rel}
    assert _git(host, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


# -- staging discipline --------------------------------------------------------


def test_commits_plan_paths_and_item_references_only(tmp_path: Path) -> None:
    layout = _own_repo(tmp_path)
    _write(layout.bundle_dir, f"{ITEM}.md", "page\n")
    _write(layout.bundle_dir, f"{ITEM}/references/guidance-plan.md", "g\n")
    _write(layout.bundle_dir, f"{ITEM}/children/index.md", "children\n")
    _write(layout.bundle_dir, f"{SIBLING}/references/orca-placement/k.json", "{}\n")
    outcome = commit_workspace(
        layout, WorkspaceCommit("workspace: advance feature-a design -> plan", items=(ITEM,)), (f"{ITEM}.md",)
    )
    assert outcome.status == "committed", outcome
    bundle = layout.bundle_dir.relative_to(layout.root).as_posix()
    assert _files_in_head(layout.root) == {f"{bundle}/{ITEM}.md", f"{bundle}/{ITEM}/references/guidance-plan.md"}
    dirty = _status(layout.root)
    assert "feature-a/children/" in dirty and "feature-b" in dirty
    assert _git(layout.root, "log", "-1", "--format=%B").strip() == "workspace: advance feature-a design -> plan"


def test_foreign_staged_file_stays_staged_and_uncommitted(tmp_path: Path) -> None:
    layout = _own_repo(tmp_path)
    _write(layout.root, "notes.txt", "human\n")
    _git(layout.root, "add", "notes.txt")
    _write(layout.bundle_dir, "work/a.md", "a\n")
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a.md",))
    assert outcome.status == "committed"
    assert "notes.txt" not in _files_in_head(layout.root)
    assert "A  notes.txt" in _status(layout.root)


def test_first_commit_in_unborn_repo_keeps_foreign_staged_file(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _init_git(layout.root, commit=False)
    _write(layout.root, "notes.txt", "human\n")
    _git(layout.root, "add", "notes.txt")
    _write(layout.bundle_dir, "work/a.md", "a\n")

    outcome = commit_workspace(layout, WorkspaceCommit("workspace: first"), ("work/a.md",))

    assert outcome.status == "committed", outcome
    assert outcome.sha == _git(layout.root, "rev-parse", "HEAD").strip()
    assert _files_in_head(layout.root) == {"okf/work/a.md"}
    assert "A  notes.txt" in _status(layout.root)


def test_foreign_dirty_file_untouched(tmp_path: Path) -> None:
    layout = _own_repo(tmp_path)
    _write(layout.bundle_dir, "concepts/x.md", "dirty\n")
    _write(layout.bundle_dir, "work/a.md", "a\n")
    commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a.md",))
    assert "?? " in _status(layout.root) and "concepts/" in _status(layout.root)


def test_deletes_and_moves_are_staged(tmp_path: Path) -> None:
    layout = _own_repo(tmp_path)
    _write(layout.bundle_dir, "work/old/references/r.md", "r\n")
    _write(layout.bundle_dir, "work/gone.md", "g\n")
    _git(layout.root, "add", "-A")
    _git(layout.root, "commit", "-q", "-m", "workspace: seed2")
    (layout.bundle_dir / "work/gone.md").unlink()
    (layout.bundle_dir / "archive").mkdir()
    (layout.bundle_dir / "work/old").rename(layout.bundle_dir / "archive/old")
    outcome = commit_workspace(
        layout, WorkspaceCommit("workspace: archive 2 items"), ("archive/old", "work/gone.md", "work/old")
    )
    assert outcome.status == "committed", outcome
    assert _status(layout.root) == ""


def test_already_staged_deletion_is_committed(tmp_path: Path) -> None:
    layout = _own_repo(tmp_path)
    _write(layout.bundle_dir, "work/gone.md", "g\n")
    _git(layout.root, "add", "-A")
    _git(layout.root, "commit", "-q", "-m", "workspace: seed2")
    (layout.bundle_dir / "work/gone.md").unlink()
    _git(layout.root, "add", "-A", "--", str(layout.bundle_dir / "work/gone.md"))

    outcome = commit_workspace(layout, WorkspaceCommit("workspace: delete"), ("work/gone.md",))

    assert outcome.status == "committed", outcome
    deleted = (layout.bundle_dir / "work/gone.md").relative_to(layout.root).as_posix()
    assert _files_in_head(layout.root) == {deleted}
    assert _status(layout.root) == ""


@pytest.mark.skipif(sys.platform == "win32", reason="shell hook")
def test_rejected_move_retry_commits_source_deletion(tmp_path: Path) -> None:
    layout = _own_repo(tmp_path)
    _write(layout.bundle_dir, "work/old/references/r.md", "r\n")
    _git(layout.root, "add", "-A")
    _git(layout.root, "commit", "-q", "-m", "workspace: seed2")
    (layout.bundle_dir / "archive").mkdir()
    (layout.bundle_dir / "work/old").rename(layout.bundle_dir / "archive/old")
    hook = layout.root / ".no-hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    paths = ("archive/old", "work/old")
    first = commit_workspace(layout, WorkspaceCommit("workspace: move"), paths)
    assert first.status == "failed"
    hook.unlink()

    retried = commit_workspace(layout, WorkspaceCommit("workspace: move"), paths)

    assert retried.status == "committed", retried
    assert _status(layout.root) == ""
    assert (layout.bundle_dir / "archive/old/references/r.md").exists()


def test_missing_pathspec_entry_is_dropped(tmp_path: Path) -> None:
    layout = _own_repo(tmp_path)
    _write(layout.bundle_dir, "work/a.md", "a\n")
    outcome = commit_workspace(
        layout, WorkspaceCommit("workspace: t", items=("work/never",), extra_paths=("work/nope.json",)), ("work/a.md",)
    )
    assert outcome.status == "committed", outcome


def test_nothing_to_commit_is_skipped_no_changes(tmp_path: Path) -> None:
    layout = _own_repo(tmp_path)
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t", items=(ITEM,)), ())
    assert (outcome.status, outcome.reason) == ("skipped", "no-changes")


# -- failure modes -------------------------------------------------------------


def test_index_lock_retries_then_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _own_repo(tmp_path)
    slept: list[float] = []
    monkeypatch.setattr(commits, "_sleep", slept.append)
    (layout.root / ".git" / "index.lock").write_text("", encoding="utf-8", newline="\n")
    _write(layout.bundle_dir, "work/a.md", "a\n")
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a.md",))
    assert outcome.status == "failed"
    assert "index.lock" in (outcome.reason or "")
    assert slept == [0.2, 0.5, 1.0]


@pytest.mark.skipif(sys.platform == "win32", reason="shell hook")
def test_rejecting_pre_commit_hook_fails(tmp_path: Path) -> None:
    layout = _own_repo(tmp_path)
    hook = layout.root / ".no-hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'hook says no' >&2\nexit 1\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    _write(layout.bundle_dir, "work/a.md", "a\n")
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a.md",))
    assert (outcome.status, outcome.reason) == ("failed", "hook says no")
    assert (layout.bundle_dir / "work/a.md").exists()


def test_missing_identity_fails_without_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _own_repo(tmp_path)
    _git(layout.root, "config", "--unset", "user.name")
    _git(layout.root, "config", "--unset", "user.email")
    _git(layout.root, "config", "user.useConfigOnly", "true")
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global"))
    _write(layout.bundle_dir, "work/a.md", "a\n")
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a.md",))
    assert isinstance(outcome, CommitOutcome)
    assert outcome.status == "failed"
    assert outcome.reason is not None and "identity" in outcome.reason.lower()


def test_literal_pathspec_does_not_include_pattern_match(tmp_path: Path) -> None:
    layout = _own_repo(tmp_path)
    _write(layout.bundle_dir, "work/a[1].md", "target\n")
    _write(layout.bundle_dir, "work/a1.md", "foreign\n")
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a[1].md",))
    assert outcome.status == "committed", outcome
    bundle = layout.bundle_dir.relative_to(layout.root).as_posix()
    assert _files_in_head(layout.root) == {f"{bundle}/work/a[1].md"}
    assert "a1.md" in _status(layout.root)


@pytest.mark.parametrize("command", ["ls-files", "ls-tree"])
def test_path_discovery_git_failure_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    layout = _own_repo(tmp_path)
    _write(layout.bundle_dir, "work/a.md", "a\n")
    original = commits.probe_git

    def fail_discovery(cwd: Path, *args: str, **kwargs: object) -> GitOutcome:
        if command in args:
            return GitOutcome(128, "", "ok", "discovery failed")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(commits, "probe_git", fail_discovery)
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a.md",))
    assert (outcome.status, outcome.reason) == ("failed", "discovery failed")
    assert _log(layout.root) == ["workspace: seed"]


def test_pathspec_cannot_escape_bundle(tmp_path: Path) -> None:
    layout = _own_repo(tmp_path)
    _write(layout.root, "notes.txt", "human\n")
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t", extra_paths=("../notes.txt",)), ())
    assert outcome.status == "failed"
    assert outcome.reason is not None and "outside" in outcome.reason
    assert _log(layout.root) == ["workspace: seed"]
    assert "notes.txt" in _status(layout.root)


def test_repository_probe_timeout_is_failed_not_no_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _own_repo(tmp_path)
    _write(layout.bundle_dir, "work/a.md", "a\n")
    monkeypatch.setattr(commits, "probe_git", lambda *args, **kwargs: GitOutcome(None, "", "timeout"))
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a.md",))
    assert (outcome.status, outcome.reason) == ("failed", "git timeout")
    assert _log(layout.root) == ["workspace: seed"]


def test_head_lookup_failure_is_reported_after_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _own_repo(tmp_path)
    _write(layout.bundle_dir, "work/a.md", "a\n")
    original = commits.probe_git

    def fail_head(cwd: Path, *args: str, **kwargs: object) -> GitOutcome:
        if args == ("rev-parse", "HEAD"):
            return GitOutcome(None, "", "timeout")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(commits, "probe_git", fail_head)
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: t"), ("work/a.md",))
    assert (outcome.status, outcome.sha, outcome.reason) == ("committed", None, "git timeout reading HEAD")
    assert _log(layout.root)[0] == "workspace: t"


def test_missing_executable_skips_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _own_repo(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "no-executables"))
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: missing git"), ())
    assert (outcome.status, outcome.reason) == ("skipped", "git missing")
    assert outcome.reason in commits.NOTE_REASONS


def test_repository_probe_error_remains_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _own_repo(tmp_path)
    monkeypatch.setattr(commits, "probe_git", lambda *args, **kwargs: GitOutcome(None, "", "error"))
    outcome = commit_workspace(layout, WorkspaceCommit("workspace: error"), ())
    assert (outcome.status, outcome.reason) == ("failed", "git error")

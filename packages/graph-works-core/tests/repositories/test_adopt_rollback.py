from __future__ import annotations

from pathlib import Path

import pytest
from adopt_fixture import adoptable
from gitrepo import git
from graph_works_core.repositories import adopt
from graph_works_core.repositories.adopt import run_repo_adopt
from repositories_okf.git import GitFailure
from workspace_fixture import NOW


def _old_layout_works(fixture) -> None:
    layout = fixture.layout
    assert fixture.source.is_dir()
    assert git(fixture.source, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(fixture.source, "rev-parse", "HEAD") == fixture.head
    for worktree in fixture.linked:
        common = Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
        assert common == (fixture.source / ".git").resolve()
    assert not (layout.worktrees_dir / "demo" / "main").exists()
    assert not (layout.bundle_dir / "repositories" / "demo").exists()
    assert not (layout.bundle_dir / "repositories" / "demo.md").exists()
    assert layout.manifest_path.read_bytes().decode("utf-8") == fixture.manifest_before
    assert git(layout.root, "status", "--porcelain") == ""
    assert git(layout.root, "log", "-1", "--format=%s") == "declare demo"


def test_a_failed_move_leaves_nothing_moved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = adoptable(tmp_path)

    def refuse(source: Path, target: Path) -> None:
        raise OSError("injected move failure")

    monkeypatch.setattr(adopt, "_move", refuse)
    result = run_repo_adopt(fixture.layout, "demo", now=NOW)
    assert result.refusal is not None and result.refusal.code == "write-failed"
    assert "injected move failure" in result.refusal.detail
    _old_layout_works(fixture)


def test_a_failed_repair_moves_the_checkout_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = adoptable(tmp_path)
    real = adopt.worktree_repair
    calls: list[Path] = []

    def fail_first(git, repo, paths):
        calls.append(repo)
        if len(calls) == 1:
            return GitFailure(cause="nonzero", command="git worktree repair", detail="injected repair failure")
        return real(git, repo, paths)

    monkeypatch.setattr(adopt, "worktree_repair", fail_first)
    result = run_repo_adopt(fixture.layout, "demo", now=NOW)
    assert result.refusal is not None and result.refusal.code == "git-failed"
    assert "rollback incomplete" not in result.refusal.detail
    _old_layout_works(fixture)


def test_a_failed_workspace_commit_undoes_page_manifest_and_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = adoptable(tmp_path)

    def failed(layout, subject, log, **kwargs):
        raise OSError("injected commit failure")  # after page, manifest and index were written

    monkeypatch.setattr(adopt, "_commit", failed)
    result = run_repo_adopt(fixture.layout, "demo", now=NOW)
    assert result.refusal is not None and result.refusal.code == "write-failed"
    _old_layout_works(fixture)


def test_a_failed_commit_outcome_is_a_failure_not_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from graph_works_core.workspace.commits import CommitOutcome

    fixture = adoptable(tmp_path)
    monkeypatch.setattr(adopt, "_commit", lambda *a, **k: (CommitOutcome("failed", None, "s", (), "injected"), ()))
    result = run_repo_adopt(fixture.layout, "demo", now=NOW)
    assert result.refusal is not None and result.refusal.code == "write-failed"
    assert "injected" in result.refusal.detail
    _old_layout_works(fixture)


def test_rollback_reuses_but_never_removes_a_preexisting_track_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = adoptable(tmp_path, linked=0)
    checkout = fixture.layout.worktrees_dir / "demo" / "main"
    checkout.parent.mkdir(parents=True, exist_ok=True)
    git(fixture.source, "switch", "-q", "--detach")
    git(fixture.source, "worktree", "add", "-q", str(checkout), "main")
    monkeypatch.setattr(adopt, "_commit", lambda *a, **k: (_ for _ in ()).throw(OSError("injected")))
    result = run_repo_adopt(fixture.layout, "demo", track="main", now=NOW)
    assert not result.ok
    assert checkout.is_dir() and git(checkout, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(fixture.source, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"  # it was detached before adopt ran


def test_failed_record_and_failed_move_back_preserve_the_only_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = adoptable(tmp_path)
    real_move = adopt._move
    clone = fixture.layout.bundle_dir / "repositories/demo/references/git"

    def move(source: Path, target: Path) -> None:
        if source == clone:
            raise OSError("injected rollback move failure")
        real_move(source, target)

    def record(*args, **kwargs):
        raise OSError("injected record failure")

    monkeypatch.setattr(adopt, "_move", move)
    monkeypatch.setattr(adopt, "_record", record)
    result = run_repo_adopt(fixture.layout, "demo", now=NOW)
    assert result.refusal is not None
    assert "rollback incomplete" in result.refusal.detail
    assert "injected rollback move failure" in result.refusal.detail
    assert clone.is_dir() and not fixture.source.exists()
    assert git(clone, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(clone, "rev-parse", "HEAD") == fixture.head
    for worktree in fixture.linked:
        assert (
            Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
            == (clone / ".git").resolve()
        )
    assert fixture.layout.manifest_path.read_bytes().decode("utf-8") == fixture.manifest_before


def test_failed_rollback_repair_is_reported_and_preserves_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = adoptable(tmp_path)
    real_repair = adopt.worktree_repair
    calls = 0

    def repair(git, repo, paths):
        nonlocal calls
        calls += 1
        if calls == 1:
            # A successful repair can precede a failure in a later step.
            return real_repair(git, repo, paths)
        return GitFailure(cause="nonzero", command="git worktree repair", detail="injected rollback repair failure")

    def record(*args, **kwargs):
        raise OSError("injected record failure")

    monkeypatch.setattr(adopt, "worktree_repair", repair)
    monkeypatch.setattr(adopt, "_record", record)
    result = run_repo_adopt(fixture.layout, "demo", now=NOW)
    assert result.refusal is not None
    assert "rollback incomplete" in result.refusal.detail
    assert "injected rollback repair failure" in result.refusal.detail
    assert fixture.source.is_dir()
    assert git(fixture.source, "rev-parse", "HEAD") == fixture.head


@pytest.mark.parametrize("operation", ["worktree_remove", "attach"])
def test_failed_rollback_git_step_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    fixture = adoptable(tmp_path)

    def record(*args, **kwargs):
        raise OSError("injected record failure")

    def fail(*args, **kwargs):
        return GitFailure(cause="nonzero", command=operation, detail="injected rollback git failure")

    monkeypatch.setattr(adopt, "_record", record)
    monkeypatch.setattr(adopt, operation, fail)
    result = run_repo_adopt(fixture.layout, "demo", now=NOW)
    assert result.refusal is not None
    assert "rollback incomplete" in result.refusal.detail
    assert "injected rollback git failure" in result.refusal.detail
    assert fixture.source.is_dir()
    assert git(fixture.source, "rev-parse", "HEAD") == fixture.head


@pytest.mark.parametrize("prior_staged", [False, True])
def test_real_failed_commit_restores_index(tmp_path: Path, prior_staged: bool) -> None:
    from dataclasses import replace

    fixture = adoptable(tmp_path)
    if prior_staged:
        fixture.layout.manifest_path.write_text(
            fixture.manifest_before + "# staged author edit\n", encoding="utf-8", newline=""
        )
        git(fixture.layout.root, "add", "workspace.yaml")
        fixture = replace(fixture, manifest_before=fixture.layout.manifest_path.read_text(encoding="utf-8"))
        # Keep a different worktree version, so restoring HEAD or re-staging cannot pass.
        fixture.layout.manifest_path.write_text(
            fixture.manifest_before + "# unstaged author edit\n", encoding="utf-8", newline=""
        )
        fixture = replace(fixture, manifest_before=fixture.layout.manifest_path.read_text(encoding="utf-8"))
    before_index = git(fixture.layout.root, "ls-files", "--stage")
    before_status = git(fixture.layout.root, "status", "--porcelain")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="")
    hook.chmod(0o755)
    git(fixture.layout.root, "config", "core.hooksPath", str(hooks))
    result = run_repo_adopt(fixture.layout, "demo", now=NOW)
    assert result.refusal is not None and result.refusal.code == "write-failed"
    assert git(fixture.layout.root, "ls-files", "--stage") == before_index
    assert git(fixture.layout.root, "status", "--porcelain") == before_status
    # Check the full old layout, keeping the deliberate staged/unstaged edits for these checks.
    git(fixture.layout.root, "reset", "-q", "HEAD", "--", "workspace.yaml")
    fixture.layout.manifest_path.write_text(
        git(fixture.layout.root, "show", "HEAD:workspace.yaml") + "\n", encoding="utf-8", newline=""
    )
    _old_layout_works(replace(fixture, manifest_before=fixture.layout.manifest_path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("step", ["_record", "_append_log"])
def test_cancellation_restores_repository_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    fixture = adoptable(tmp_path)

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt("cancel adoption")

    monkeypatch.setattr(adopt, step, cancel)
    with pytest.raises(KeyboardInterrupt, match="cancel adoption"):
        run_repo_adopt(fixture.layout, "demo", now=NOW)
    _old_layout_works(fixture)


def test_metadata_snapshot_and_restore_stay_inside_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    fixture = adoptable(tmp_path)
    concurrent_before = fixture.manifest_before + "# concurrent before acquisition\n"
    concurrent_after = concurrent_before + "# concurrent after release\n"
    real_lock = adopt.held_bundle_lock

    @contextmanager
    def intervening_lock(layout):
        layout.manifest_path.write_text(concurrent_before, encoding="utf-8", newline="")
        try:
            with real_lock(layout):
                try:
                    yield
                finally:
                    # Restoration must already be complete while ownership is held.
                    assert layout.manifest_path.read_text(encoding="utf-8") == concurrent_before
        finally:
            layout.manifest_path.write_text(concurrent_after, encoding="utf-8", newline="")

    def fail(*args, **kwargs):
        raise OSError("record failed")

    monkeypatch.setattr(adopt, "held_bundle_lock", intervening_lock)
    monkeypatch.setattr(adopt, "_append_log", fail)
    result = run_repo_adopt(fixture.layout, "demo", now=NOW)
    assert not result.ok
    assert fixture.source.is_dir()
    assert fixture.layout.manifest_path.read_text(encoding="utf-8") == concurrent_after


def test_cancellation_after_commit_keeps_published_adoption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    fixture = adoptable(tmp_path)
    real_lock = adopt.held_bundle_lock

    @contextmanager
    def cancel_on_release(layout):
        with real_lock(layout):
            yield
        raise KeyboardInterrupt("after publication")

    monkeypatch.setattr(adopt, "held_bundle_lock", cancel_on_release)
    with pytest.raises(KeyboardInterrupt, match="after publication"):
        run_repo_adopt(fixture.layout, "demo", now=NOW)
    assert not fixture.source.exists()
    assert (fixture.layout.bundle_dir / "repositories/demo/references/git").is_dir()
    assert git(fixture.layout.root, "status", "--porcelain") == ""
    assert git(fixture.layout.root, "log", "-1", "--format=%s") == "workspace: adopt managed repository demo"


def test_cancellation_reading_head_after_commit_keeps_published_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from graph_works_core.workspace import commits

    fixture = adoptable(tmp_path)
    real_probe = commits.probe_git

    def cancel_post_commit(cwd, *args, **kwargs):
        if args == ("rev-parse", "HEAD"):
            assert git(fixture.layout.root, "log", "-1", "--format=%s") == "workspace: adopt managed repository demo"
            raise KeyboardInterrupt("post-commit HEAD read")
        return real_probe(cwd, *args, **kwargs)

    monkeypatch.setattr(commits, "probe_git", cancel_post_commit)
    with pytest.raises(KeyboardInterrupt, match="post-commit HEAD read"):
        run_repo_adopt(fixture.layout, "demo", now=NOW)
    clone = fixture.layout.bundle_dir / "repositories/demo/references/git"
    assert clone.is_dir() and not fixture.source.exists()
    assert git(clone, "rev-parse", "HEAD") == fixture.head
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert git(fixture.layout.worktrees_dir / "demo/main", "symbolic-ref", "--short", "HEAD") == "main"
    for worktree in fixture.linked:
        assert (
            Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve() == clone / ".git"
        )
    assert "path: okf/repositories/demo/references/git" in fixture.layout.manifest_path.read_text(encoding="utf-8")
    assert (fixture.layout.bundle_dir / "repositories/demo.md").is_file()
    assert git(fixture.layout.root, "status", "--porcelain") == ""

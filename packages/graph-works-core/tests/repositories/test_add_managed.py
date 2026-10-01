from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from gitrepo import Upstream, git, make_upstream
from graph_works_core.repositories import commands
from graph_works_core.repositories.commands import run_repo_add
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import declared_repositories
from okf_io import load
from repositories_okf.git import GitFailure
from repositories_okf.pin import read_pin
from workspace_fixture import NOW, bundle_bytes, make_workspace, porcelain


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    return make_workspace(tmp_path)


@pytest.fixture
def upstream(tmp_path: Path) -> Upstream:
    (tmp_path / "up").mkdir()
    up = make_upstream(tmp_path / "up")
    up.commit({"README.md": "# demo\n", "src/a.py": "a = 1\n"}, "c1")
    return up


def _clone(layout: WorkspaceLayout) -> Path:
    return layout.bundle_dir / "repositories" / "demo" / "references" / "git"


def test_add_managed_writes_page_clone_checkout_manifest_index_and_log_in_one_commit(
    layout: WorkspaceLayout, upstream: Upstream
) -> None:
    head = git(upstream.work, "rev-parse", "HEAD")
    before = git(layout.root, "rev-list", "--count", "HEAD")
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW, nonce="t1")
    assert result.ok, result.refusal
    checkout = layout.worktrees_dir / "demo" / "main"
    assert (result.managed, result.track, result.commit, result.checkout) == (
        True,
        "main",
        head,
        ".gw/worktrees/demo/main",
    )
    assert git(_clone(layout), "rev-parse", "HEAD") == head
    assert git(_clone(layout), "status", "--porcelain", "--branch").startswith("## HEAD (no branch)")
    assert git(checkout, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    page = load(layout.bundle_dir / "repositories" / "demo.md")
    assert page.fm_data(dates="iso")["type"] == "ManagedRepository"
    pin = read_pin(page)
    assert pin is not None and (pin.commit, pin.ref, pin.previous, pin.generation) == (head, "main", None, None)
    assert not (layout.bundle_dir / "repositories" / "demo" / "snapshots").exists()
    assert not (layout.bundle_dir / "repositories" / "demo" / "changelog.md").exists()
    assert "demo.md" in (layout.bundle_dir / "repositories" / "index.md").read_text(encoding="utf-8")
    assert "**repo-add** demo — " in (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert declared_repositories(layout)["demo"] == checkout.resolve()
    assert int(git(layout.root, "rev-list", "--count", "HEAD")) == int(before) + 1
    assert git(layout.root, "log", "-1", "--format=%s") == "workspace: add managed repository demo"
    committed = git(layout.root, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert "workspace.yaml" in committed and not any("/references/git/" in path for path in committed)
    assert porcelain(layout) == ""


def test_add_managed_on_a_non_default_track(layout: WorkspaceLayout, upstream: Upstream) -> None:
    git(upstream.work, "switch", "-q", "-c", "develop")
    develop = upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    git(upstream.work, "push", "-q", "origin", "HEAD:develop")
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, track="develop", now=NOW)
    assert result.ok and result.commit == develop and result.checkout == ".gw/worktrees/demo/develop"
    assert git(layout.worktrees_dir / "demo" / "develop", "rev-parse", "--abbrev-ref", "HEAD") == "develop"


def test_an_explicit_checkout_path(layout: WorkspaceLayout, upstream: Upstream, tmp_path: Path) -> None:
    elsewhere = tmp_path / "work" / "demo"
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, checkout=elsewhere, now=NOW)
    assert result.ok and result.checkout == elsewhere.resolve().as_posix()
    assert declared_repositories(layout)["demo"] == elsewhere.resolve()


def test_dry_run_reports_and_writes_nothing(layout: WorkspaceLayout, upstream: Upstream) -> None:
    before = bundle_bytes(layout)
    manifest = layout.manifest_path.read_bytes()
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW, dry_run=True)
    assert result.ok and result.dry_run and result.checkout == ".gw/worktrees/demo/main"
    assert "repositories/demo.md" in result.paths
    assert bundle_bytes(layout) == before and layout.manifest_path.read_bytes() == manifest
    assert not (layout.worktrees_dir / "demo").exists()


def test_repo_declared_and_checkout_exists_refuse_before_cloning(layout: WorkspaceLayout, upstream: Upstream) -> None:
    layout.manifest_path.write_text(
        layout.manifest_path.read_text(encoding="utf-8").replace(
            "repositories:\n", "repositories:\n  demo:\n    path: code\n", 1
        ),
        encoding="utf-8",
        newline="",
    )
    declared = run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW)
    assert declared.refusal is not None and declared.refusal.code == "repo-declared"
    occupied = layout.worktrees_dir / "other" / "main"
    occupied.mkdir(parents=True)
    exists = run_repo_add(layout, upstream.url, name="other", managed=True, now=NOW)
    assert exists.refusal is not None and exists.refusal.code == "checkout-exists"
    assert not (layout.bundle_dir / "repositories" / "demo").exists()
    assert not (layout.bundle_dir / "repositories" / "other").exists()


def test_a_failure_after_the_worktree_leaves_no_worktree_clone_or_manifest_change(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = bundle_bytes(layout)
    manifest = layout.manifest_path.read_bytes()

    def boom(*_args: object, **_kwargs: object) -> str | None:
        raise OSError("disk full")

    monkeypatch.setattr(commands, "_append_log", boom)
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW)
    assert result.refusal is not None and result.refusal.code == "write-failed"
    assert bundle_bytes(layout) == before
    assert layout.manifest_path.read_bytes() == manifest
    assert not (layout.worktrees_dir / "demo" / "main").exists()
    assert not (layout.bundle_dir / "repositories" / "demo").exists()
    assert porcelain(layout) == ""
    monkeypatch.undo()
    assert run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW).ok


def test_ref_with_managed_and_checkout_without_it_are_caller_errors(
    layout: WorkspaceLayout, upstream: Upstream, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="ref"):
        run_repo_add(layout, upstream.url, managed=True, ref="main", now=NOW)
    with pytest.raises(ValueError, match="checkout"):
        run_repo_add(layout, upstream.url, checkout=tmp_path / "x", now=NOW)


def test_failed_materialize_preserves_a_foreign_clone_directory(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = _clone(layout)

    def fail_materialize(*_args: object, **_kwargs: object) -> GitFailure:
        foreign.mkdir(parents=True)
        (foreign / "marker").write_text("foreign", encoding="utf-8", newline="")
        return GitFailure("nonzero", "git materialize", "destination already exists")

    monkeypatch.setattr(commands, "materialize", fail_materialize)
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW)
    assert result.refusal is not None and result.refusal.code == "git-failed"
    assert (foreign / "marker").read_text(encoding="utf-8") == "foreign"


def test_checkout_created_before_operation_lock_is_refused_without_cloning(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = layout.worktrees_dir / "demo" / "main"
    original_lock = commands.locked

    @contextmanager
    def occupied_at_lock(path: Path) -> Iterator[None]:
        with original_lock(path):
            target.mkdir(parents=True)
            (target / "marker").write_text("foreign", encoding="utf-8", newline="")
            yield

    monkeypatch.setattr(commands, "locked", occupied_at_lock)
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW)
    assert result.refusal is not None and result.refusal.code == "checkout-exists"
    assert (target / "marker").read_text(encoding="utf-8") == "foreign"
    assert not _clone(layout).exists()


def test_failed_worktree_add_preserves_a_foreign_checkout(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = layout.worktrees_dir / "demo" / "main"

    def fail_link(_git: object, _clone: Path, dest: Path, _branch: str) -> GitFailure:
        dest.mkdir(parents=True)
        (dest / "marker").write_text("foreign", encoding="utf-8", newline="")
        return GitFailure("nonzero", "git worktree add", "destination already exists")

    monkeypatch.setattr(commands, "worktree_add", fail_link)
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW)
    assert result.refusal is not None and result.refusal.code == "git-failed"
    assert (target / "marker").read_text(encoding="utf-8") == "foreign"
    assert not _clone(layout).exists()


def test_failed_worktree_add_removes_a_checkout_registered_to_our_clone(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_link = commands.worktree_add

    def link_then_fail(git_runner: commands.Git, clone: Path, dest: Path, branch: str) -> GitFailure:
        assert original_link(git_runner, clone, dest, branch) is None
        return GitFailure("nonzero", "git worktree add", "failed after registration")

    monkeypatch.setattr(commands, "worktree_add", link_then_fail)
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW)
    assert result.refusal is not None and result.refusal.code == "git-failed"
    assert not (layout.worktrees_dir / "demo" / "main").exists()
    assert not _clone(layout).exists()


def test_manifest_snapshot_read_failure_rolls_back_the_owned_worktree(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_before = layout.manifest_path.read_bytes()
    original = Path.read_bytes
    original_link = commands.worktree_add
    linked = False
    reads_after_link = 0

    def link_then_fail_read(git_runner: commands.Git, clone: Path, dest: Path, branch: str) -> GitFailure | None:
        nonlocal linked
        result = original_link(git_runner, clone, dest, branch)
        linked = result is None
        return result

    def fail_manifest_read(path: Path) -> bytes:
        nonlocal reads_after_link
        if linked and path == layout.manifest_path:
            reads_after_link += 1
            if reads_after_link == 2:
                raise OSError("manifest read failed")
        return original(path)

    monkeypatch.setattr(commands, "worktree_add", link_then_fail_read)
    monkeypatch.setattr(Path, "read_bytes", fail_manifest_read)
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW)
    assert result.refusal is not None and result.refusal.code == "write-failed"
    assert original(layout.manifest_path) == manifest_before
    assert not (layout.worktrees_dir / "demo" / "main").exists()
    assert not _clone(layout).exists()


def test_failed_write_preserves_a_manifest_change_before_bundle_lock(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_lock = commands.held_bundle_lock
    original_manifest = layout.manifest_path.read_bytes()

    @contextmanager
    def changed_before_lock(_layout: WorkspaceLayout) -> Iterator[None]:
        with original_lock(_layout):
            with layout.manifest_path.open("ab") as stream:
                stream.write(b"# concurrent edit\n")
            yield

    def fail_log(*_args: object, **_kwargs: object) -> str | None:
        raise OSError("disk full")

    monkeypatch.setattr(commands, "held_bundle_lock", changed_before_lock)
    monkeypatch.setattr(commands, "_append_log", fail_log)
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW)
    assert result.refusal is not None and result.refusal.code == "write-failed"
    assert layout.manifest_path.read_bytes() == original_manifest + b"# concurrent edit\n"
    assert not (layout.worktrees_dir / "demo" / "main").exists()


def test_rejected_commit_rolls_back_files_index_and_owned_git_resources(
    layout: WorkspaceLayout, upstream: Upstream
) -> None:
    unrelated = layout.root / "unrelated.txt"
    unrelated.write_text("staged\n", encoding="utf-8", newline="")
    git(layout.root, "add", "unrelated.txt")
    before = bundle_bytes(layout)
    manifest = layout.manifest_path.read_bytes()
    staged = git(layout.root, "diff", "--cached", "--binary")
    head = git(layout.root, "rev-parse", "HEAD")
    hook = layout.root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="")
    hook.chmod(0o755)
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW)
    assert not result.ok and result.refusal is not None and result.refusal.code == "write-failed"
    assert bundle_bytes(layout) == before
    assert layout.manifest_path.read_bytes() == manifest
    assert git(layout.root, "diff", "--cached", "--binary") == staged
    assert git(layout.root, "rev-parse", "HEAD") == head
    assert not _clone(layout).exists()
    assert not (layout.worktrees_dir / "demo" / "main").exists()
    assert porcelain(layout) == "A  unrelated.txt"


@pytest.mark.parametrize("dry_run", [False, True])
def test_managed_track_chooses_branch_over_same_named_tag(
    layout: WorkspaceLayout, upstream: Upstream, dry_run: bool
) -> None:
    git(upstream.work, "tag", "main")
    tip = upstream.commit({"src/a.py": "a = 2\n"}, "branch ahead of tag")
    git(upstream.work, "push", "-q", "origin", "refs/tags/main")
    result = run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW, dry_run=dry_run)
    assert result.ok, result.refusal
    assert result.commit == tip
    if not dry_run:
        assert git(_clone(layout), "rev-parse", "HEAD") == tip
        assert git(layout.worktrees_dir / "demo" / "main", "rev-parse", "HEAD") == tip
        assert read_pin(load(layout.bundle_dir / "repositories/demo.md")).commit == tip

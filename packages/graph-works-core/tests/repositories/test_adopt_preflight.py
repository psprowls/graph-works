from __future__ import annotations

import os
from pathlib import Path

import pytest
from adopt_fixture import adoptable, snapshot
from gitrepo import git
from graph_works_core.repositories import adopt
from graph_works_core.repositories.adopt import run_repo_adopt
from workspace_fixture import NOW


def _refused(fixture, code: str, **kwargs) -> None:
    before = snapshot(fixture)
    result = run_repo_adopt(fixture.layout, kwargs.pop("name", "demo"), now=NOW, **kwargs)
    assert result.refusal is not None and result.refusal.code == code, result.refusal
    assert not result.ok
    assert snapshot(fixture) == before


def test_dry_run_reports_the_plan_and_writes_nothing(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    before = snapshot(fixture)
    result = run_repo_adopt(fixture.layout, "demo", now=NOW, dry_run=True)
    assert result.ok and result.dry_run
    assert (result.track, result.commit) == ("main", fixture.head)
    assert result.clone == "okf/repositories/demo/references/git"
    assert (
        result.checkout
        == Path(os.path.relpath(fixture.layout.worktrees_dir / "demo" / "main", fixture.layout.root)).as_posix()
    )
    assert result.checkout_created is True
    assert set(result.repaired) == {str(p) for p in fixture.linked}
    assert "workspace.yaml" in result.paths and "repositories/demo.md" in result.paths
    assert snapshot(fixture) == before


def test_unknown_repository(tmp_path: Path) -> None:
    _refused(adoptable(tmp_path), "unknown-repository", name="nope")


def test_local_override(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    fixture.layout.local_manifest_path.write_text(
        "repositories:\n  demo:\n    path: /elsewhere\n", encoding="utf-8", newline=""
    )
    _refused(fixture, "local-override")


def test_not_a_primary_checkout_missing_or_linked(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    text = fixture.manifest_before.replace("path: ../demo", f"path: {fixture.linked[0]}")
    fixture.layout.manifest_path.write_text(text, encoding="utf-8", newline="")
    _refused(fixture, "not-a-primary-checkout")
    fixture.layout.manifest_path.write_text(
        fixture.manifest_before.replace("path: ../demo", "path: ../gone"), encoding="utf-8", newline=""
    )
    _refused(fixture, "not-a-primary-checkout")


def test_already_in_bundle(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    text = fixture.manifest_before.replace("path: ../demo", "path: okf/somewhere")
    fixture.layout.manifest_path.write_text(text, encoding="utf-8", newline="")
    _refused(fixture, "already-in-bundle")


@pytest.mark.parametrize("occupant", ["repositories/demo.md", "repositories/demo/references/git/x"])
def test_lane_occupied(tmp_path: Path, occupant: str) -> None:
    fixture = adoptable(tmp_path)
    target = fixture.layout.bundle_dir / occupant
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n", encoding="utf-8", newline="")
    _refused(fixture, "lane-occupied")


def test_not_ignored(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    gitignore = fixture.layout.root / ".gitignore"
    kept = [line for line in gitignore.read_text(encoding="utf-8").splitlines() if "references/git" not in line]
    gitignore.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="")
    git(fixture.layout.root, "commit", "-qam", "drop lane ignore")
    _refused(fixture, "not-ignored")


def test_checkout_dirty_tracked_or_untracked_but_not_ignored(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    (fixture.source / "stray.txt").write_text("x\n", encoding="utf-8", newline="")
    _refused(fixture, "checkout-dirty")
    (fixture.source / "stray.txt").unlink()
    (fixture.source / "README.md").write_text("edited\n", encoding="utf-8", newline="")
    _refused(fixture, "checkout-dirty")


def test_worktree_dirty(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    (fixture.linked[1] / "stray.txt").write_text("x\n", encoding="utf-8", newline="")
    _refused(fixture, "worktree-dirty")


def test_no_origin(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    git(fixture.source, "remote", "remove", "origin")
    _refused(fixture, "no-origin")


def test_no_track_and_track_mismatch(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    _refused(fixture, "track-mismatch", track="develop")
    git(fixture.source, "switch", "-q", "--detach")
    _refused(fixture, "no-track")


def test_track_worktree_conflict(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    squatter = fixture.layout.worktrees_dir / "demo" / "main"
    squatter.mkdir(parents=True)
    _refused(fixture, "track-worktree-conflict")


def test_track_checked_out_elsewhere(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    git(fixture.source, "switch", "-q", "--detach")
    git(fixture.linked[1], "switch", "-q", "main")
    _refused(fixture, "track-checked-out", track="main")


def test_cross_device(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = adoptable(tmp_path)
    monkeypatch.setattr(adopt, "_same_device", lambda a, b: False)
    _refused(fixture, "cross-device")


def test_cwd_inside_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = adoptable(tmp_path)
    monkeypatch.chdir(fixture.source / "src")
    _refused(fixture, "cwd-inside-checkout")


def test_manifest_unsupported(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    text = fixture.manifest_before.replace("    ignore:\n", "    checkout: somewhere\n    ignore:\n")
    fixture.layout.manifest_path.write_text(text, encoding="utf-8", newline="")
    git(fixture.layout.root, "commit", "-qam", "stray checkout")
    _refused(fixture, "manifest-unsupported")


def test_workspace_unversioned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = adoptable(tmp_path)
    monkeypatch.setattr(adopt, "_ignored", lambda layout, relative: None)
    _refused(fixture, "workspace-unversioned")


def test_now_must_be_aware(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    with pytest.raises(ValueError):
        run_repo_adopt(fixture.layout, "demo", now=NOW.replace(tzinfo=None))


def test_bootstrap_reuses_track_checkout_and_ignored_files_are_allowed(tmp_path: Path) -> None:
    fixture = adoptable(tmp_path)
    git(fixture.source, "switch", "-q", "--detach")
    checkout = fixture.layout.worktrees_dir / "demo" / "main"
    checkout.parent.mkdir(parents=True)
    git(fixture.source, "worktree", "add", "-q", str(checkout), "main")
    git(fixture.source, "config", "core.excludesFile", str(tmp_path / "ignore"))
    (tmp_path / "ignore").write_text("ignored.txt\n", encoding="utf-8", newline="")
    (fixture.source / "ignored.txt").write_text("local\n", encoding="utf-8", newline="")
    before = snapshot(fixture)
    result = run_repo_adopt(fixture.layout, "demo", track="main", now=NOW, dry_run=True)
    assert result.ok and not result.checkout_created
    assert str(checkout) in result.repaired
    assert snapshot(fixture) == before


def test_prunable_worktree_is_reported_without_repair(tmp_path: Path) -> None:
    import shutil

    fixture = adoptable(tmp_path)
    shutil.rmtree(fixture.linked[1])
    before = snapshot(fixture)
    result = run_repo_adopt(fixture.layout, "demo", now=NOW, dry_run=True)
    assert result.ok
    assert result.repaired == (str(fixture.linked[0]),)
    assert result.warnings == (f"skipped prunable worktree {fixture.linked[1]} (run git worktree prune)",)
    assert snapshot(fixture) == before

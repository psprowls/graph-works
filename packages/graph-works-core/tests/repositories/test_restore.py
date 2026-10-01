from __future__ import annotations

from pathlib import Path

import pytest
from gitrepo import Upstream, git, make_upstream
from graph_works_core.repositories.commands import run_repo_add, run_repo_restore
from graph_works_core.workspace.layout import WorkspaceLayout
from repositories_okf.git import remove_tree
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


def _clone(layout: WorkspaceLayout, name: str = "demo") -> Path:
    return layout.bundle_dir / "repositories" / name / "references" / "git"


def _outcomes(result: object) -> dict[str, tuple[str, str | None]]:
    return {o.name: (o.outcome, o.refusal.code if o.refusal else None) for o in result.outcomes}  # type: ignore[attr-defined]


def test_a_deleted_clone_is_rebuilt_at_exactly_the_pin(layout: WorkspaceLayout, upstream: Upstream) -> None:
    pinned = run_repo_add(layout, upstream.url, name="demo", now=NOW).commit
    upstream.commit({"src/a.py": "a = 2\n"}, "c2")  # upstream moves on; restore must not follow it
    commits = git(layout.root, "rev-list", "--count", "HEAD")
    remove_tree(_clone(layout))
    result = run_repo_restore(layout)
    assert result.ok and _outcomes(result) == {"demo": ("cloned", None)}
    assert git(_clone(layout), "rev-parse", "HEAD") == pinned
    assert git(layout.root, "rev-list", "--count", "HEAD") == commits  # restore never commits
    assert porcelain(layout) == ""
    assert _outcomes(run_repo_restore(layout)) == {"demo": ("present", None)}


def test_a_moved_head_is_redetached(layout: WorkspaceLayout, upstream: Upstream) -> None:
    pinned = run_repo_add(layout, upstream.url, name="demo", now=NOW).commit
    newer = upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    git(_clone(layout), "fetch", "-q", "origin", "main")
    git(_clone(layout), "checkout", "-q", "--detach", newer)
    assert _outcomes(run_repo_restore(layout)) == {"demo": ("redetached", None)}
    assert git(_clone(layout), "rev-parse", "HEAD") == pinned


def test_refusals_are_per_repository(layout: WorkspaceLayout, upstream: Upstream) -> None:
    run_repo_add(layout, upstream.url, name="dirty", now=NOW)
    run_repo_add(layout, upstream.url, name="moved", now=NOW)
    run_repo_add(layout, upstream.url, name="gone", now=NOW)
    newer = upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    git(_clone(layout, "dirty"), "fetch", "-q", "origin", "main")
    git(_clone(layout, "dirty"), "checkout", "-q", "--detach", newer)
    (_clone(layout, "dirty") / "scratch.txt").write_text("x\n", encoding="utf-8", newline="")
    git(_clone(layout, "moved"), "remote", "set-url", "origin", "https://example.com/elsewhere.git")
    remove_tree(_clone(layout, "gone"))
    result = run_repo_restore(layout)
    assert not result.ok
    assert _outcomes(result) == {
        "dirty": ("refused", "clone-dirty"),
        "moved": ("refused", "url-mismatch"),
        "gone": ("cloned", None),
    }


def test_no_pin_unreachable_pin_and_unknown_names(layout: WorkspaceLayout, upstream: Upstream) -> None:
    lane = layout.bundle_dir / "repositories"
    lane.mkdir(exist_ok=True)
    (lane / "unpinned.md").write_text(
        f"---\ntype: ReferenceRepository\ntitle: unpinned\ndescription: d\n"
        f"url: {upstream.url}\n---\n\n## Summary\n\nx\n",
        encoding="utf-8",
        newline="",
    )
    (lane / "lost.md").write_text(
        f"---\ntype: ReferenceRepository\ntitle: lost\ndescription: d\nurl: {upstream.url}\n"
        f"pin:\n  commit: {'f' * 40}\n  fetched_at: '2026-09-29T20:40:00Z'\n---\n\n## Summary\n"
        f"\nx\n",
        encoding="utf-8",
        newline="",
    )
    result = run_repo_restore(layout, ["unpinned", "lost", "nope"])
    assert _outcomes(result) == {
        "unpinned": ("refused", "no-pin"),
        "lost": ("refused", "pin-unreachable"),
        "nope": ("refused", "not-found"),
    }
    assert not _clone(layout, "lost").exists()


def test_a_managed_page_without_checkout_refuses_before_cloning(layout: WorkspaceLayout, upstream: Upstream) -> None:
    head = git(upstream.work, "rev-parse", "HEAD")
    (layout.bundle_dir / "repositories").mkdir(exist_ok=True)
    (layout.bundle_dir / "repositories" / "managed.md").write_text(
        f"---\ntype: ManagedRepository\ntitle: managed\ndescription: d\nurl: {upstream.url}\n"
        f"pin:\n  commit: {head}\n  fetched_at: '2026-09-29T20:40:00Z'\n---\n\n## Summary\n\n"
        f"x\n",
        encoding="utf-8",
        newline="",
    )
    assert _outcomes(run_repo_restore(layout, ["managed"])) == {"managed": ("cloned", "no-checkout")}
    assert not _clone(layout, "managed").exists()


def test_dry_run_reports_and_changes_nothing(layout: WorkspaceLayout, upstream: Upstream) -> None:
    run_repo_add(layout, upstream.url, name="demo", now=NOW)
    remove_tree(_clone(layout))
    before = bundle_bytes(layout)
    result = run_repo_restore(layout, dry_run=True)
    assert result.dry_run and _outcomes(result) == {"demo": ("cloned", None)}
    assert bundle_bytes(layout) == before and not _clone(layout).exists()


def test_an_empty_lane_is_an_empty_ok_result(layout: WorkspaceLayout) -> None:
    result = run_repo_restore(layout)
    assert result.ok and result.outcomes == ()


def test_git_unavailable_is_one_refusal(layout: WorkspaceLayout, upstream: Upstream, tmp_path: Path) -> None:
    (layout.bundle_dir / "repositories").mkdir(exist_ok=True)
    (layout.bundle_dir / "repositories" / "managed.md").write_text(
        f"---\ntype: ManagedRepository\ntitle: managed\ndescription: d\nurl: {upstream.url}\n"
        f"pin:\n  commit: {'f' * 40}\n  fetched_at: '2026-09-29T20:40:00Z'\n---\n\n## Summary\n"
        f"\nx\n",
        encoding="utf-8",
        newline="",
    )
    result = run_repo_restore(layout, environ={"GW_GIT": str(tmp_path / "no-git"), "PATH": ""})
    assert result.refusal is not None and result.refusal.code == "git-unavailable" and not result.ok


def test_restore_continues_after_a_clone_filesystem_failure(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repositories_okf.git import remove_tree

    assert run_repo_add(layout, upstream.url, name="first", now=NOW).ok
    assert run_repo_add(layout, upstream.url, name="second", now=NOW).ok
    remove_tree(_clone(layout, "first"))
    remove_tree(_clone(layout, "second"))
    original = Path.mkdir

    def mkdir(path, *args, **kwargs):
        if path == _clone(layout, "first"):
            raise OSError("permission denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    result = run_repo_restore(layout, ["first", "second"])
    assert _outcomes(result) == {"first": ("refused", "git-failed"), "second": ("cloned", None)}
    assert not result.ok

"""D-001: an in-bundle repository behaves as if its checkout were its path."""

from __future__ import annotations

import subprocess
from pathlib import Path

from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.orchestrate.stage_advance import _resolve_repo
from okf_io import load, load_bundle
from test_orchestrate_shell import _git_repo, _workspace, _write
from work_tracker_okf.items import IGNORE, load_items

ITEM = "work/feature-a"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _in_bundle(tmp_path: Path, *, track: str = "main"):
    """A workspace whose `code` repository is a clone with a linked checkout on its track."""
    upstream = _git_repo(tmp_path / "upstream")
    if track != "main":
        _git(upstream, "branch", "-m", track)
    root = tmp_path / "ws"
    layout = _workspace(root, "version: 1\n")
    clone = layout.bundle_dir / "repositories/code/references/git"
    clone.parent.mkdir(parents=True)
    _git(tmp_path, "clone", "-q", str(upstream), str(clone))
    _git(clone, "checkout", "-q", "--detach")
    checkout = layout.worktrees_dir / "code" / track
    checkout.parent.mkdir(parents=True)
    _git(clone, "worktree", "add", "-q", str(checkout), track)
    manifest = layout.manifest_path
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + f"repositories:\n  code:\n    path: {clone.relative_to(root).as_posix()}\n"
        + f"    checkout: {checkout.relative_to(root).as_posix()}\n",
        encoding="utf-8",
        newline="",
    )
    return layout, clone, checkout


def _ordinary(tmp_path: Path, *, track: str = "main"):
    """The same shape with the checkout declared directly as `path`."""
    code = _git_repo(tmp_path / "code")
    if track != "main":
        _git(code, "branch", "-m", track)
        _git(code, "update-ref", f"refs/remotes/origin/{track}", track)
        _git(code, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{track}")
    layout = _workspace(tmp_path / "ws", f"version: 1\nrepositories:\n  code:\n    path: {code}\n")
    return layout, code


def _stamp(layout, item: str, worktree: Path, branch: str = "main") -> None:
    document = load(layout.bundle_dir / f"{item}.md")
    document.set("worktree", str(worktree))
    document.set("branch", branch)
    document.save()


def _action(result) -> tuple[str, str | None, str | None]:
    if not result.plan.dispatches:
        return ("blocked:" + ",".join(blocked.kind for blocked in result.blocked), None, None)
    (dispatch,) = result.plan.dispatches
    return dispatch.worktree.action, dispatch.worktree.branch, dispatch.worktree.base_branch


def test_an_execute_item_stamped_at_the_checkout_plans_main_there(tmp_path: Path) -> None:
    layout, _clone, checkout = _in_bundle(tmp_path / "a")
    _write(layout, ITEM, phase="execute", work_status="accepted")
    _stamp(layout, ITEM, checkout)
    result = orchestrate.run_orchestrate(layout, ITEM)
    (dispatch,) = result.plan.dispatches
    assert result.code_repo == str(checkout)
    assert dispatch.worktree.path == str(checkout)
    assert _action(result) == ("main", "main", None)

    ordinary, code = _ordinary(tmp_path / "b")
    _write(ordinary, ITEM, phase="execute", work_status="accepted")
    _stamp(ordinary, ITEM, code)
    assert _action(result) == _action(orchestrate.run_orchestrate(ordinary, ITEM))


def test_a_cold_start_root_plans_like_an_ordinary_repository(tmp_path: Path) -> None:
    track = "release"
    layout, _clone, checkout = _in_bundle(tmp_path / "a", track=track)
    _write(layout, ITEM, phase="execute", work_status="accepted")
    ordinary, _code = _ordinary(tmp_path / "b", track=track)
    _write(ordinary, ITEM, phase="execute", work_status="accepted")
    in_bundle, plain = orchestrate.run_orchestrate(layout, ITEM), orchestrate.run_orchestrate(ordinary, ITEM)
    assert _action(in_bundle) == _action(plain)
    assert _action(in_bundle)[0] == "create-top-level"
    assert _action(in_bundle)[2] == track
    assert in_bundle.code_repo == str(checkout)


def test_the_dirty_guard_inspects_the_checkout_not_the_clone(tmp_path: Path) -> None:
    """Compare to an ordinary repository to avoid depending on the guard's action policy."""
    ordinary, code = _ordinary(tmp_path / "plain")
    _write(ordinary, ITEM, phase="execute", work_status="accepted")
    _stamp(ordinary, ITEM, code)
    plain_clean = _action(orchestrate.run_orchestrate(ordinary, ITEM))
    (code / "dirty.txt").write_text("x\n", encoding="utf-8", newline="")
    plain_dirty = _action(orchestrate.run_orchestrate(ordinary, ITEM))
    assert plain_clean != plain_dirty

    layout, clone, checkout = _in_bundle(tmp_path / "bundle")
    _write(layout, ITEM, phase="execute", work_status="accepted")
    _stamp(layout, ITEM, checkout)
    (clone / "only-in-clone.txt").write_text("x\n", encoding="utf-8", newline="")
    assert _action(orchestrate.run_orchestrate(layout, ITEM)) == plain_clean
    (checkout / "dirty.txt").write_text("x\n", encoding="utf-8", newline="")
    dirty = orchestrate.run_orchestrate(layout, ITEM)
    assert _action(dirty) == plain_dirty
    assert dirty.code_repo == str(checkout)


def test_advance_matches_a_cwd_in_the_checkout_or_in_the_clone(tmp_path: Path) -> None:
    layout, clone, checkout = _in_bundle(tmp_path)
    other = _git_repo(tmp_path / "other")
    layout.manifest_path.write_text(
        layout.manifest_path.read_text(encoding="utf-8") + f"  other:\n    path: {other}\n",
        encoding="utf-8",
        newline="",
    )
    _write(layout, ITEM, phase="execute", work_status="accepted")
    items = tuple(load_items(load_bundle(layout.bundle_dir, ignore=IGNORE)))
    item = next(candidate for candidate in items if candidate.path == ITEM)
    for cwd in (checkout, checkout / "packages/a", clone):
        repo, declared = _resolve_repo(layout, items, item, repo_name=None, cwd=cwd)
        assert (repo.name, repo.path, repo.source) == ("code", checkout, "cwd")
        assert checkout in declared and clone not in declared
    repo, _ = _resolve_repo(layout, items, item, repo_name=None, cwd=other)
    assert repo.name == "other"

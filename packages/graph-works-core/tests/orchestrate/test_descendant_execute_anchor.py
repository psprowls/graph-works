"""An unstamped descendant's execute facts come from its enclosing integration anchor, never trunk."""

from __future__ import annotations

import subprocess
from pathlib import Path

from _gate_helpers import gate_ready
from graph_works_core.orchestrate import stage_advance as stage
from test_orchestrate_shell import TODAY, _initialized_workspace, _ready, _refused_inertly, _write

EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/bug-a"


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _trunk(tmp_path: Path) -> tuple[Path, str]:
    """A main checkout left at the baseline commit."""
    repo = tmp_path / "code"
    (repo / "packages/a").mkdir(parents=True)
    _run(repo, "init", "-b", "main")
    _run(repo, "config", "user.email", "t@example.com")
    _run(repo, "config", "user.name", "T")
    (repo / "packages/a/x.py").write_text("one\n", encoding="utf-8")
    _run(repo, "add", ".")
    _run(repo, "commit", "-m", "first")
    return repo, _run(repo, "rev-parse", "HEAD")


def _anchor(repo: Path, tmp_path: Path, branch: str = "epic/probe", name: str = "anchor") -> Path:
    """A linked worktree carrying one affected commit while main stays put."""
    anchor = tmp_path / name
    _run(repo, "worktree", "add", "-b", branch, str(anchor))
    (anchor / "packages/a/x.py").write_text(f"{branch}\n", encoding="utf-8")
    _run(anchor, "commit", "-am", f"work on {branch}")
    return anchor


def _stamp(anchor: Path, branch: str = "epic/probe") -> str:
    return f"worktree: {anchor}\nbranch: {branch}\n"


def _epic(layout, *, extra: str, path: str = EPIC) -> None:
    _write(layout, path, type="Epic", phase="execute", work_status="in-progress", extra=extra)


def _child(layout, base: str, *, path: str = CHILD, extra: str = "") -> None:
    _ready(layout, path, extra=f"start_sha: {base}\n{extra}", **{})  # type: ignore[arg-type]


def _scenario(tmp_path: Path):
    layout = _initialized_workspace(tmp_path)
    repo, base = _trunk(tmp_path)
    anchor = _anchor(repo, tmp_path)
    _epic(layout, extra=_stamp(anchor))
    _child(layout, base)
    return layout, repo, base, anchor


def test_commit_gate_and_results_read_the_enclosing_anchor(tmp_path: Path) -> None:
    layout, repo, _base, anchor = _scenario(tmp_path)
    gate_ready(layout, repo, CHILD, tree_of=anchor)
    result = stage.run_stage_advance(layout, CHILD, today=TODAY, repo=repo, infer_worktree=False, dry_run=False)
    assert result.outcome.plan.refusal is None, result.outcome.plan.detail
    assert result.results_path is not None
    assert _run(anchor, "rev-parse", "--short=7", "HEAD") in result.results_path.read_text(encoding="utf-8")
    page = (layout.bundle_dir / f"{CHILD}.md").read_text(encoding="utf-8")
    assert "worktree:" not in page


def test_a_receipt_for_trunk_only_does_not_satisfy_the_anchor_tree(tmp_path: Path) -> None:
    layout, repo, _base, _anchor_path = _scenario(tmp_path)
    gate_ready(layout, repo, CHILD)
    result = stage.run_stage_advance(layout, CHILD, today=TODAY, repo=repo, infer_worktree=False, dry_run=False)
    _refused_inertly(layout, CHILD, result, "no-gate-receipt")


def test_the_nearest_owner_wins_over_an_outer_one(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, base = _trunk(tmp_path)
    outer, inner = _anchor(repo, tmp_path, "epic/outer", "outer"), _anchor(repo, tmp_path, "epic/inner", "inner")
    _epic(layout, extra=_stamp(outer, "epic/outer"))
    nested = f"{EPIC}/children/epic-b"
    _epic(layout, path=nested, extra=_stamp(inner, "epic/inner"))
    child = f"{nested}/children/bug-a"
    _child(layout, base, path=child)
    gate_ready(layout, repo, child, tree_of=inner)
    result = stage.run_stage_advance(layout, child, today=TODAY, repo=repo, infer_worktree=False, dry_run=False)
    assert result.outcome.plan.refusal is None, result.outcome.plan.detail
    assert _run(inner, "rev-parse", "--short=7", "HEAD") in result.results_path.read_text(encoding="utf-8")  # type: ignore[union-attr]


def test_a_nearest_owner_without_a_stamp_refuses_despite_a_valid_outer_one(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, base = _trunk(tmp_path)
    outer = _anchor(repo, tmp_path, "epic/outer", "outer")
    _epic(layout, extra=_stamp(outer, "epic/outer"))
    nested = f"{EPIC}/children/epic-b"
    _epic(layout, path=nested, extra="")
    child = f"{nested}/children/bug-a"
    _child(layout, base, path=child)
    gate_ready(layout, repo, child, tree_of=outer)
    result = stage.run_stage_advance(layout, child, today=TODAY, repo=repo, infer_worktree=False, dry_run=False)
    _refused_inertly(layout, child, result, "worktree-missing")
    assert nested in result.outcome.plan.detail


def test_a_descendant_without_an_owner_refuses(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, base = _trunk(tmp_path)
    _write(layout, "work/bug-p", type="Bug", phase="execute", work_status="in-progress")
    _child(layout, base, path="work/bug-p/children/bug-a")
    gate_ready(layout, repo, "work/bug-p/children/bug-a")
    result = stage.run_stage_advance(
        layout, "work/bug-p/children/bug-a", today=TODAY, repo=repo, infer_worktree=False, dry_run=False
    )
    _refused_inertly(layout, "work/bug-p/children/bug-a", result, "worktree-missing")


def test_an_invalid_owner_stamp_refuses_instead_of_reading_trunk(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, base = _trunk(tmp_path)
    anchor = _anchor(repo, tmp_path)
    _epic(layout, extra=_stamp(anchor, "epic/other"))
    _child(layout, base)
    gate_ready(layout, repo, CHILD, tree_of=anchor)
    result = stage.run_stage_advance(layout, CHILD, today=TODAY, repo=repo, infer_worktree=False, dry_run=False)
    _refused_inertly(layout, CHILD, result, "worktree-missing")
    assert "not verified" in result.outcome.plan.detail


def test_a_bypass_does_not_authorize_reading_trunk(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, base = _trunk(tmp_path)
    _epic(layout, extra="")
    _child(layout, base)
    result = stage.run_stage_advance(
        layout,
        CHILD,
        today=TODAY,
        repo=repo,
        infer_worktree=False,
        skip_gate="worktree-missing",
        skip_reason="test",
        actor="psprowls",
        dry_run=True,
    )
    assert result.results_path is None


def test_a_valid_recorded_item_worktree_wins_over_the_ancestor(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, base = _trunk(tmp_path)
    anchor = _anchor(repo, tmp_path)
    own = _anchor(repo, tmp_path, "bug/own", "own")
    _epic(layout, extra=_stamp(anchor))
    _child(layout, base, extra=_stamp(own, "bug/own"))
    gate_ready(layout, repo, CHILD, tree_of=own)
    result = stage.run_stage_advance(layout, CHILD, today=TODAY, repo=repo, infer_worktree=False, dry_run=False)
    assert result.outcome.plan.refusal is None, result.outcome.plan.detail
    assert _run(own, "rev-parse", "--short=7", "HEAD") in result.results_path.read_text(encoding="utf-8")  # type: ignore[union-attr]


def test_a_missing_recorded_item_worktree_never_falls_back_to_the_ancestor(tmp_path: Path) -> None:
    layout, repo, _base, _anchor_path = _scenario(tmp_path)
    _child(layout, _base, extra=_stamp(tmp_path / "gone", "bug/gone"))
    result = stage.run_stage_advance(layout, CHILD, today=TODAY, repo=repo, infer_worktree=False, dry_run=False)
    _refused_inertly(layout, CHILD, result, "worktree-missing")
    assert "no longer exists" in result.outcome.plan.detail


def test_dirt_under_affects_in_the_anchor_refuses_uncommitted_work(tmp_path: Path) -> None:
    layout, repo, _base, anchor = _scenario(tmp_path)
    (anchor / "packages/a/x.py").write_text("dirty\n", encoding="utf-8")
    result = stage.run_stage_advance(layout, CHILD, today=TODAY, repo=repo, infer_worktree=False, dry_run=False)
    _refused_inertly(layout, CHILD, result, "uncommitted-work")


def test_a_top_level_item_still_falls_back_to_the_declared_checkout(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, base = _trunk(tmp_path)
    (repo / "packages/a/x.py").write_text("two\n", encoding="utf-8")
    _run(repo, "commit", "-am", "second")
    _ready(layout, "work/bug-top", extra=f"start_sha: {base}\n")
    gate_ready(layout, repo, "work/bug-top")
    result = stage.run_stage_advance(
        layout, "work/bug-top", today=TODAY, repo=repo, infer_worktree=False, dry_run=False
    )
    assert result.outcome.plan.refusal is None, result.outcome.plan.detail

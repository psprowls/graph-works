"""A descendant's commit gate measures its own computed worktree against its owner's branch."""

from __future__ import annotations

import subprocess
from pathlib import Path

from _gate_helpers import gate_ready, place_item
from gw_orchestrate import stage_advance as stage
from test_orchestrate_shell import TODAY, _initialized_workspace, _ready, _refused_inertly, _write

EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/bug-a"


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _trunk(tmp_path: Path) -> Path:
    """A main checkout holding one commit."""
    repo = tmp_path / "code"
    (repo / "packages/a").mkdir(parents=True)
    _run(repo, "init", "-b", "main")
    _run(repo, "config", "user.email", "t@example.com")
    _run(repo, "config", "user.name", "T")
    (repo / "packages/a/x.py").write_text("one\n", encoding="utf-8")
    _run(repo, "add", ".")
    _run(repo, "commit", "-m", "first")
    return repo


def _scenario(tmp_path: Path):
    layout = _initialized_workspace(tmp_path)
    repo = _trunk(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute", work_status="in-progress")
    _ready(layout, CHILD, type="Bug")  # type: ignore[arg-type]
    return layout, repo


def test_the_commit_gate_and_results_read_the_childs_own_worktree(tmp_path: Path) -> None:
    layout, repo = _scenario(tmp_path)
    child = place_item(layout, repo, CHILD)
    gate_ready(layout, repo, CHILD, tree_of=child)
    result = stage.run_stage_advance(layout, CHILD, today=TODAY, repo=repo, dry_run=False)
    assert result.outcome.plan.refusal is None, result.outcome.plan.detail
    assert result.results_path is not None
    assert _run(child, "rev-parse", "--short=7", "HEAD") in result.results_path.read_text(encoding="utf-8")
    page = (layout.bundle_dir / f"{CHILD}.md").read_text(encoding="utf-8")
    assert "worktree:" not in page


def test_a_receipt_for_trunk_only_does_not_satisfy_the_childs_tree(tmp_path: Path) -> None:
    layout, repo = _scenario(tmp_path)
    place_item(layout, repo, CHILD)
    gate_ready(layout, repo, CHILD)
    result = stage.run_stage_advance(layout, CHILD, today=TODAY, repo=repo, dry_run=False)
    _refused_inertly(layout, CHILD, result, "no-gate-receipt")


def test_a_commit_only_on_the_owner_branch_is_not_the_childs_work(tmp_path: Path) -> None:
    layout, repo = _scenario(tmp_path)
    owner = place_item(layout, repo, EPIC)
    (owner / "packages/a/x.py").write_text("owner\n", encoding="utf-8")
    _run(owner, "commit", "-am", "owner work")
    place_item(layout, repo, CHILD, commit=False)
    result = stage.run_stage_advance(layout, CHILD, today=TODAY, repo=repo, dry_run=False)
    _refused_inertly(layout, CHILD, result, "no-commits")


def test_a_missing_child_worktree_never_falls_back_to_the_owner(tmp_path: Path) -> None:
    layout, repo = _scenario(tmp_path)
    place_item(layout, repo, EPIC)
    gate_ready(layout, repo, CHILD)
    result = stage.run_stage_advance(layout, CHILD, today=TODAY, repo=repo, dry_run=False)
    _refused_inertly(layout, CHILD, result, "worktree-missing")
    detail = result.outcome.plan.detail
    assert detail.startswith(f"{CHILD}: ") and "is not a checkout of" in detail
    assert detail.endswith(f"; run gw work place {CHILD}")


def test_dirt_under_affects_in_the_childs_worktree_refuses_uncommitted_work(tmp_path: Path) -> None:
    layout, repo = _scenario(tmp_path)
    child = place_item(layout, repo, CHILD)
    (child / "packages/a/x.py").write_text("dirty\n", encoding="utf-8")
    result = stage.run_stage_advance(layout, CHILD, today=TODAY, repo=repo, dry_run=False)
    _refused_inertly(layout, CHILD, result, "uncommitted-work")


def test_a_bypass_does_not_authorize_reading_trunk(tmp_path: Path) -> None:
    layout, repo = _scenario(tmp_path)
    gate_ready(layout, repo, CHILD)
    result = stage.run_stage_advance(
        layout,
        CHILD,
        today=TODAY,
        repo=repo,
        skip_gate="worktree-missing",
        skip_reason="test",
        actor="psprowls",
        dry_run=True,
    )
    assert result.results_path is None


def test_a_live_bypass_writes_no_results_stub_from_trunk(tmp_path: Path) -> None:
    layout, repo = _scenario(tmp_path)
    gate_ready(layout, repo, CHILD)
    result = stage.run_stage_advance(
        layout,
        CHILD,
        today=TODAY,
        repo=repo,
        skip_gate="worktree-missing",
        skip_reason="test",
        actor="psprowls",
        dry_run=False,
    )
    assert result.results_path is None
    assert not list(layout.bundle_dir.rglob("03-execute-results.md"))


def test_a_top_level_item_reads_its_own_computed_worktree(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo = _trunk(tmp_path)
    _ready(layout, "work/bug-top", type="Bug")  # type: ignore[arg-type]
    top = place_item(layout, repo, "work/bug-top")
    gate_ready(layout, repo, "work/bug-top", tree_of=top)
    result = stage.run_stage_advance(layout, "work/bug-top", today=TODAY, repo=repo, dry_run=False)
    assert result.outcome.plan.refusal is None, result.outcome.plan.detail


def test_a_commit_on_trunk_is_not_the_items_work(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo = _trunk(tmp_path)
    _ready(layout, "work/bug-top", type="Bug")  # type: ignore[arg-type]
    place_item(layout, repo, "work/bug-top", commit=False)
    (repo / "packages/a/x.py").write_text("trunk\n", encoding="utf-8")
    _run(repo, "commit", "-am", "trunk work")
    result = stage.run_stage_advance(layout, "work/bug-top", today=TODAY, repo=repo, dry_run=False)
    _refused_inertly(layout, "work/bug-top", result, "no-commits")


def test_advance_from_any_cwd_writes_no_placement(tmp_path: Path, monkeypatch) -> None:
    layout = _initialized_workspace(tmp_path)
    repo = _trunk(tmp_path)
    path = "work/bug-top"
    _ready(layout, path, type="Bug")  # type: ignore[arg-type]
    top = place_item(layout, repo, path)
    gate_ready(layout, repo, path, tree_of=top)
    monkeypatch.chdir(tmp_path)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, dry_run=False)
    assert result.outcome.plan.refusal is None, result.outcome.plan.detail
    page = (layout.bundle_dir / f"{path}.md").read_text(encoding="utf-8")
    assert "worktree:" not in page and "branch:" not in page


def _top(tmp_path: Path, *, commit: bool):
    layout = _initialized_workspace(tmp_path)
    repo = _trunk(tmp_path)
    path = "work/bug-top"
    _ready(layout, path, type="Bug")  # type: ignore[arg-type]
    return layout, repo, path, place_item(layout, repo, path, commit=commit)


def _parent_advances_and_refreshes(repo: Path, wt: Path, member: str = "packages/a/y.py") -> None:
    (repo / member).write_text("parent\n", encoding="utf-8")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-m", "parent work")
    _run(wt, "merge", "--no-ff", "-m", "refresh", "main")


def test_a_refresh_merge_alone_is_no_commits(tmp_path: Path) -> None:
    layout, repo, path, wt = _top(tmp_path, commit=False)
    _parent_advances_and_refreshes(repo, wt)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, dry_run=False)
    _refused_inertly(layout, path, result, "no-commits")


def test_an_item_commit_plus_a_refresh_merge_passes(tmp_path: Path) -> None:
    layout, repo, path, wt = _top(tmp_path, commit=True)
    _parent_advances_and_refreshes(repo, wt)
    gate_ready(layout, repo, path, tree_of=wt)
    result = stage.run_stage_advance(layout, path, today=TODAY, repo=repo, dry_run=False)
    assert result.outcome.plan.refusal is None, result.outcome.plan.detail


def test_a_parent_side_change_under_affects_is_not_touched(tmp_path: Path) -> None:
    from gw_kernel.workspace import provenance

    layout, repo, _path, wt = _top(tmp_path, commit=True)
    _parent_advances_and_refreshes(repo, wt)
    git = provenance.gate_git(layout)
    assert not isinstance(git, provenance.GitFailure)
    facts = provenance.strict_branch_range(wt, base_ref="main", paths=["packages/a"], git=git)
    assert isinstance(facts, provenance.GateRange)
    assert facts.files == ("packages/a/x.py",)


def test_no_start_sha_is_not_a_gate_code(tmp_path: Path) -> None:
    from work_tracker_okf.advance import COMMIT_GATE_REFUSALS

    assert "no-start-sha" not in COMMIT_GATE_REFUSALS
    layout, repo, path, _wt = _top(tmp_path, commit=True)
    result = stage.run_stage_advance(
        layout, path, today=TODAY, repo=repo, dry_run=False, skip_gate="no-start-sha", skip_reason="r", actor="pat"
    )
    assert result.outcome.plan.refusal == "gate-bypass-invalid"

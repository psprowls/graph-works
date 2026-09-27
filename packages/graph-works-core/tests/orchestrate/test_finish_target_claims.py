"""Finish admission claims the worktree a finish merges into, not only its source."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.workspace.finish import FinishPlan, FinishTarget
from graph_works_core.workspace.repos import ItemRepo
from test_orchestrate_plan import _branch_tail_rules, _item, _plan
from work_tracker_okf.items import Stamp

ROOT = "work/epic-x"
A, B = f"{ROOT}/children/feature-a", f"{ROOT}/children/feature-b"
REPO = ItemRepo("code", Path("/repo"), "frontmatter")
RELAY = _branch_tail_rules()


def _items(b_affects=("packages/b",)):
    return (
        _item(ROOT, type="Epic", phase="execute", child_paths=(A, B), worktree="/epic", branch="epic/x"),
        _item(
            A, phase="finish", work_status="in-progress", affects=("packages/a",), worktree="/wt/a", branch="feature/a"
        ),
        _item(B, phase="finish", work_status="in-progress", affects=b_affects, worktree="/wt/b", branch="feature/b"),
    )


def _target(source: str, branch: str, into: str | None = "/epic") -> FinishTarget:
    return FinishTarget(REPO, source, branch, "epic/x", into)


def _run(finish_plans, *, items=None, **kw):
    return _plan(
        items if items is not None else _items(),
        ROOT,
        dispatch_rules=RELAY,
        finish_plans=finish_plans,
        worktree_exists={"/wt/a": True, "/wt/b": True, "/epic": True},
        **kw,
    )


def test_first_accepted_finish_reserves_the_target_in_one_pass() -> None:
    plans = {
        A: FinishPlan((_target("/wt/a", "feature/a"),), ()),
        B: FinishPlan((_target("/wt/b", "feature/b"),), ()),
    }
    result = _run(plans)
    assert [d.slug for d in result.dispatches] == [A]
    assert [(b.path, b.kind) for b in result.blocked] == [(B, "worktree-pending")]


def test_live_finish_blocks_only_the_candidate_on_the_same_target() -> None:
    plans = {
        A: FinishPlan((_target("/wt/a", "feature/a"),), ()),
        B: FinishPlan((_target("/wt/b", "feature/b"),), ()),
    }
    result = _run(plans, live=(orchestrate.session_name(A, "Feature", "finish"),))
    assert result.dispatches == ()
    assert [(b.path, b.kind) for b in result.blocked] == [(B, "worktree-pending")]
    assert f"/epic held by {A}" in result.blocked[0].reason
    assert result.blocked[0].reason.count(f"/epic held by {A}") == 1


@pytest.mark.parametrize("current_phase", ["finish", "execute", "done"])
def test_a_live_finish_keeps_its_target_after_revalidation_fails(current_phase) -> None:
    plans = {
        A: FinishPlan((), ("enclosing anchor dirty",)),
        B: FinishPlan((_target("/wt/b", "feature/b"),), ()),
    }
    items = _items()
    items = (
        items[0],
        replace(items[1], phase=current_phase, work_status="resolved" if current_phase == "done" else "in-progress"),
        items[2],
    )
    result = _run(plans, items=items, live=(orchestrate.session_name(A, "Feature", "finish"),))
    assert [(b.path, b.kind) for b in result.blocked] == [(B, "worktree-pending")]


def test_a_live_finish_keeps_failed_target_when_another_repo_still_validates() -> None:
    items = _items()
    items = (replace(items[0], repo_stamps={"ui": Stamp("/ui/epic", "epic/x")}), *items[1:])
    ui = ItemRepo("ui", Path("/ui"), "frontmatter")
    plans = {
        A: FinishPlan((FinishTarget(ui, "/ui/a", "feature/a", "epic/x", "/ui/epic"),), ("core anchor dirty",)),
        B: FinishPlan((_target("/wt/b", "feature/b"),), ()),
    }
    result = _run(plans, items=items, live=(orchestrate.session_name(A, "Feature", "finish"),))
    assert result.dispatches == ()
    assert [(b.path, b.kind) for b in result.blocked] == [(B, "worktree-pending")]
    assert f"/epic held by {A}" in result.blocked[0].reason


def test_a_refused_first_candidate_does_not_starve_the_second() -> None:
    plans = {
        A: FinishPlan((), ("unverified target",)),
        B: FinishPlan((_target("/wt/b", "feature/b"),), ()),
    }
    result = _run(plans, max_parallel=1)
    assert [d.slug for d in result.dispatches] == [B]
    assert [(b.path, b.kind) for b in result.blocked] == [(A, "worktree-unprovable")]


def test_a_profile_refused_first_candidate_does_not_starve_the_second() -> None:
    plans = {
        A: FinishPlan((_target("/wt/a", "feature/a"),), ()),
        B: FinishPlan((_target("/wt/b", "feature/b"),), ()),
    }
    result = _plan(
        _items(),
        ROOT,
        dispatch_rules=(),
        finish_plans=plans,
        worktree_exists={"/wt/a": True, "/wt/b": True, "/epic": True},
    )
    assert {(b.path, b.kind) for b in result.blocked} == {(A, "relay-untailed"), (B, "relay-untailed")}


def test_different_target_worktrees_stay_independent() -> None:
    plans = {
        A: FinishPlan((_target("/wt/a", "feature/a"),), ()),
        B: FinishPlan((_target("/wt/b", "feature/b", into="/other"),), ()),
    }
    result = _run(plans, live=(orchestrate.session_name(A, "Feature", "finish"),))
    assert [d.slug for d in result.dispatches] == [B]


def test_multi_target_finish_blocks_when_any_target_is_held() -> None:
    ui = ItemRepo("ui", Path("/ui"), "frontmatter")
    plans = {
        A: FinishPlan((FinishTarget(ui, "/ui/a", "feature/a", "epic/x", "/ui/epic"),), ()),
        B: FinishPlan(
            (
                _target("/wt/b", "feature/b", into="/epic2"),
                FinishTarget(ui, "/ui/b", "feature/b", "epic/x", "/ui/epic"),
            ),
            (),
        ),
    }
    result = _run(plans, live=(orchestrate.session_name(A, "Feature", "finish"),))
    assert [(b.path, b.kind) for b in result.blocked] == [(B, "worktree-pending")]
    assert "/ui/epic held by" in result.blocked[0].reason


def test_foreign_only_finish_claims_its_foreign_target() -> None:
    ui = ItemRepo("ui", Path("/ui"), "frontmatter")
    plans = {
        A: FinishPlan((FinishTarget(ui, "/ui/a", "feature/a", "epic/x", "/ui/epic"),), ()),
        B: FinishPlan((FinishTarget(ui, "/ui/b", "feature/b", "epic/x", "/ui/epic"),), ()),
    }
    result = _run(plans)
    assert [d.slug for d in result.dispatches] == [A]
    assert [(b.path, b.kind) for b in result.blocked] == [(B, "worktree-pending")]


def test_missing_live_target_checkout_evidence_refuses_mutable_admission() -> None:
    plans = {
        A: FinishPlan((_target("/wt/a", "feature/a", into=None),), ()),
        B: FinishPlan((_target("/wt/b", "feature/b", into="/other"),), ()),
    }
    result = _run(plans, live=(orchestrate.session_name(A, "Feature", "finish"),))
    assert result.dispatches == ()
    assert [(b.path, b.kind) for b in result.blocked] == [(B, "worktree-unprovable")]
    assert A in result.blocked[0].reason

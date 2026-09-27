"""Planner admission order follows the run's dependency graph (D-003)."""

from __future__ import annotations

import dataclasses
import itertools
from unittest import mock

import pytest
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.orchestrate.rank import dependent_counts
from test_orchestrate_plan import _GONE_STAMP, _RESERVE_ROOT, _blocked_kinds, _item, _overlapping_children, _plan
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.items import WorkItem

ROOT = "work/epic-probe"
CONTRACT = f"{ROOT}/children/feature-z-contract"
RIVAL = f"{ROOT}/children/feature-a-execute"
DEPENDENTS = tuple(f"{ROOT}/children/feature-dependent-{i}" for i in range(4))
_PLACEMENT: dict[str, object] = {
    "worktree_inventory": {"epic/root": "/wt/root", "feature/other": "/wt/other"},
    "worktree_exists": {"/wt/root": True, "/wt/other": True},
}


def _needs_contract(path: str, **overrides: object) -> WorkItem:
    return _item(
        path,
        phase="execute",
        work_status="accepted",
        has_plan_artifact=True,
        dependency_edges=(DependencyEdge(CONTRACT, blocks="execute", needs="resolved"),),
        **overrides,
    )


def _contract_run(*dependents: WorkItem) -> tuple[WorkItem, ...]:
    """The auto-drive reproduction: an open design everyone waits on, versus an
    accepted execute sibling that sorts ahead of it on status alone."""
    children = (CONTRACT, RIVAL, *(item.path for item in dependents))
    return (
        _item(
            ROOT,
            type="Epic",
            phase="execute",
            worktree="/wt/root",
            branch="epic/root",
            child_paths=children,
            affects=("packages/root",),
        ),
        _item(CONTRACT, phase="design", opened="2026-09-25"),
        _item(
            RIVAL,
            phase="execute",
            work_status="accepted",
            has_plan_artifact=True,
            worktree="/wt/other",
            branch="feature/other",
        ),
        *dependents,
    )


def test_regression_the_upstream_contract_wins_the_only_slot() -> None:
    items = _contract_run(*(_needs_contract(path) for path in DEPENDENTS))
    result = _plan(items, ROOT, max_parallel=1, **_PLACEMENT)
    assert [dispatch.slug for dispatch in result.dispatches] == [CONTRACT]
    assert result.dispatches[0].worktree.action == orchestrate.READER_ACTION
    assert _blocked_kinds(result) == {(RIVAL, "capacity"), *((path, "deps") for path in DEPENDENTS)}


@pytest.mark.parametrize("affects", [("packages/a",), (), ("gw:workspace",)])
def test_regression_the_contract_design_dispatches_beside_a_live_overlapping_execute(
    affects: tuple[str, ...],
) -> None:
    """A design holds no affects claim, so it starts beside a live sibling execute."""
    root, contract, rival = _contract_run()
    items = (root, dataclasses.replace(contract, affects=affects), dataclasses.replace(rival, affects=affects))
    live = (orchestrate.session_name(RIVAL, "Feature", "execute"),)
    result = _plan(items, ROOT, max_parallel=2, live=live, **_PLACEMENT)
    assert [dispatch.slug for dispatch in result.dispatches] == [CONTRACT]
    assert result.dispatches[0].worktree.action == orchestrate.READER_ACTION
    assert result.blocked == ()


def test_an_execute_accepted_earlier_in_the_plan_does_not_block_a_later_design() -> None:
    """An accepted execute reserves code, while a later design holds none."""
    result = _plan(_contract_run(), ROOT, max_parallel=2, **_PLACEMENT)
    assert [dispatch.slug for dispatch in result.dispatches] == [RIVAL, CONTRACT]
    assert result.blocked == ()


def test_without_dependents_the_status_order_is_unchanged() -> None:
    """The accepted rival wins the only slot; the design holds no affects claim,
    so it is refused for capacity rather than affects-overlap (epic D-002)."""
    result = _plan(_contract_run(), ROOT, max_parallel=1, **_PLACEMENT)
    assert [dispatch.slug for dispatch in result.dispatches] == [RIVAL]
    assert (CONTRACT, "capacity") in _blocked_kinds(result)


@pytest.mark.parametrize("rank", [0, 1])
@pytest.mark.parametrize(
    ("contract_status", "contract_opened", "rival_status", "rival_opened", "winner"),
    [
        ("accepted", "2026-09-26", "open", "2026-08-01", CONTRACT),
        ("open", "2026-08-01", "open", "2026-09-26", CONTRACT),
        ("open", "2026-08-01", "open", "2026-08-01", RIVAL),
    ],
)
def test_equal_ranks_preserve_status_opened_path_order(
    rank: int, contract_status: str, contract_opened: str, rival_status: str, rival_opened: str, winner: str
) -> None:
    dependents: tuple[WorkItem, ...] = ()
    if rank:
        dependent_on_rival = dataclasses.replace(
            _needs_contract(DEPENDENTS[1]),
            dependency_edges=(DependencyEdge(RIVAL, blocks="execute", needs="resolved"),),
        )
        dependents = (_needs_contract(DEPENDENTS[0]), dependent_on_rival)
    root, contract, rival, *leaves = _contract_run(*dependents)
    items = (
        root,
        dataclasses.replace(contract, work_status=contract_status, opened=contract_opened),
        dataclasses.replace(rival, work_status=rival_status, opened=rival_opened),
        *leaves,
    )
    counts = dependent_counts(items, ROOT)
    assert counts[CONTRACT] == counts[RIVAL] == rank
    result = _plan(items, ROOT, max_parallel=1, **_PLACEMENT)
    assert [dispatch.slug for dispatch in result.dispatches] == [winner]


def test_high_rank_cannot_override_own_dependency_blocker() -> None:
    blocked = f"{ROOT}/children/feature-b-blocked"
    missing = "work/feature-unresolved"
    blocked_item = _item(
        blocked,
        phase="execute",
        work_status="accepted",
        has_plan_artifact=True,
        dependency_edges=(DependencyEdge(missing, blocks="execute", needs="resolved"),),
    )
    leaves = tuple(
        dataclasses.replace(
            _needs_contract(path),
            dependency_edges=(DependencyEdge(blocked, blocks="execute", needs="resolved"),),
        )
        for path in DEPENDENTS[:2]
    )
    items = _contract_run(blocked_item, *leaves)
    counts = dependent_counts(items, ROOT)
    assert counts[blocked] == 2 > counts[RIVAL] == 0

    result = _plan(items, ROOT, max_parallel=1, **_PLACEMENT)
    assert [dispatch.slug for dispatch in result.dispatches] == [RIVAL]
    assert (blocked, "deps") in _blocked_kinds(result)
    assert all((leaf.path, "deps") in _blocked_kinds(result) for leaf in leaves)


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_admission_order_does_not_depend_on_input_order(order: tuple[int, ...]) -> None:
    items = _contract_run(*(_needs_contract(path) for path in DEPENDENTS[:2]))
    epic, rest = items[0], items[1:]
    shuffled = (epic, *(rest[i] for i in order), *rest[3:])
    result = _plan(shuffled, ROOT, max_parallel=1, **_PLACEMENT)
    assert [dispatch.slug for dispatch in result.dispatches] == [CONTRACT]


def test_held_and_live_dependents_still_confer_rank_but_are_not_dispatched() -> None:
    held, live_dependent = DEPENDENTS[:2]
    items = _contract_run(_needs_contract(held), _needs_contract(live_dependent, affects=("packages/elsewhere",)))
    holds = {held: HoldFact(path=held, decision_id="D-001", shape="question", phase=None)}
    live = (orchestrate.session_name(live_dependent, "Feature", "execute"),)
    result = _plan(items, ROOT, max_parallel=2, holds=holds, live=live, **_PLACEMENT)
    assert [dispatch.slug for dispatch in result.dispatches] == [CONTRACT]
    # One free slot (two minus the live dependent): only the rank from the
    # held and live dependents puts the contract ahead of the accepted rival.
    # Their own gating is unchanged -- rank never makes them dispatchable.
    assert _blocked_kinds(result) == {(RIVAL, "capacity"), (held, "decisions"), (live_dependent, "deps")}


def test_ranking_never_displaces_live_work() -> None:
    items = _contract_run(*(_needs_contract(path) for path in DEPENDENTS))
    live = (orchestrate.session_name(RIVAL, "Feature", "execute"),)
    result = _plan(items, ROOT, max_parallel=1, live=live, **_PLACEMENT)
    assert result.dispatches == ()
    assert result.live == live
    assert RIVAL not in {blocked.path for blocked in result.blocked}


def test_a_refused_high_rank_candidate_reserves_nothing_and_lets_the_next_through() -> None:
    items, (upstream, rival, dependent) = _overlapping_children(
        "feature-z-up", "feature-a-rival", "feature-m-dep", first=_GONE_STAMP
    )
    edge = DependencyEdge(upstream, blocks="execute", needs="resolved")
    items = (*items[:3], dataclasses.replace(items[3], dependency_edges=(edge,)))
    real = orchestrate._resolve_worktree
    considered: list[str] = []

    def resolve(item: WorkItem, **kwargs: object) -> object:
        considered.append(item.path)
        return real(item, **kwargs)  # type: ignore[arg-type]

    with mock.patch.object(orchestrate, "_resolve_worktree", side_effect=resolve):
        result = _plan(items, _RESERVE_ROOT, max_parallel=1, worktree_exists={"/wt/gone": False, "/wt/epic": True})
    assert considered[:2] == [upstream, rival]
    assert [dispatch.slug for dispatch in result.dispatches] == [rival]
    assert _blocked_kinds(result) == {(upstream, "worktree-unprovable"), (dependent, "deps")}

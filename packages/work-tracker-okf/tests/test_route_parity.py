"""The interpreter against today's router over the full matrix (design §8).

Exclusions are by name, each asserting the difference is exactly the named one:
X4 variant (stage compared, variant not), X5 PLAN_OR_EXECUTE (phase None plus
path_candidates), X6 a hand-edited phase off a candidate path (legacy
dispatched; the interpreter blocks), X7 a completion that enters execute from a
stage other than plan (legacy left `work_status` unset; the interpreter marks it
`accepted`, the state plan completion would have set). §7 item 1 (children-terminal) changes
`advance`, not `route()`, so it needs no exclusion here.
"""

from __future__ import annotations

import dataclasses
import sys
from itertools import product
from pathlib import Path

import pytest
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.dependencies import DependencyEdge, DependencyFact, DependencyIssue
from work_tracker_okf.hierarchy import ChildRollup
from work_tracker_okf.pipeline import PACKAGED_DEFINITION, resolve_path
from work_tracker_okf.vocabulary import EFFORTS, PHASES, TYPES, WORK_STATUSES
from work_tracker_okf.workflow import RouteState, blocker_messages, route, route_attributes

sys.path.insert(0, str(Path(__file__).parent))
from _legacy_route import LEGACY_PLAN_OR_EXECUTE, legacy_route

_OPEN = ("work/x/children/bug-open",)
_ROLLUPS = [
    (None, ()),
    (ChildRollup(total=2, terminal=2, open_paths=()), ()),
    (ChildRollup(total=2, terminal=1, open_paths=_OPEN), _OPEN),
]


def _states(phase: str | None):
    for type_, effort, spec, plan, stale, status, (rollup, open_), branch in product(
        sorted(TYPES),
        [None, *sorted(EFFORTS)],
        (False, True),
        (False, True),
        (False, True),
        sorted(WORK_STATUSES),
        _ROLLUPS,
        (False, True),
    ):
        yield RouteState(
            type=type_,
            work_status=status,
            phase=phase,
            effort=effort,
            has_spec_doc=spec,
            has_plan_doc=plan,
            stale_spec=("work/sibling",) if stale else (),
            child_rollup=rollup,
            open_descendants=open_,
            has_branch=branch,
        )


def _x6(state: RouteState, kinds: set[str]) -> bool:
    return "phase-off-path" in kinds or (
        state.phase is not None and bool(kinds & {"effort-required", "attribute-required"})
    )


def _assert_parity(state: RouteState) -> str:
    legacy = legacy_route(state)
    new = route(state)
    kinds = {blocker.kind for blocker in new.blockers}
    if _x6(state, kinds):
        candidates = resolve_path(PACKAGED_DEFINITION, route_attributes(state)).candidates
        assert any(state.phase not in c.stages for c in candidates), state
        return "x6"
    assert (new.dispatch.stage if new.dispatch else None) == (legacy.dispatch.stage if legacy.dispatch else None), state
    assert blocker_messages(new) == legacy.blockers, state
    assert new.on_dispatch == legacy.on_dispatch, state
    assert new.on_return == legacy.on_return, state
    assert new.repair == legacy.repair, state
    if legacy.on_complete is not None and legacy.on_complete.phase == LEGACY_PLAN_OR_EXECUTE:
        assert new.on_complete == dataclasses.replace(legacy.on_complete, phase=None), state
        assert {c.stages for c in new.path_candidates} == {
            ("design", "plan", "execute", "finish"),
            ("design", "execute", "finish"),
        }
        return "x5"
    stage = new.dispatch.stage if new.dispatch else None
    if (
        legacy.on_complete is not None
        and legacy.on_complete.phase == "execute"
        and legacy.on_complete.work_status is None
        and stage not in (None, "plan")
    ):
        assert new.on_complete == dataclasses.replace(legacy.on_complete, work_status="accepted"), state
        assert new.path_candidates == (), state
        return "x7"
    assert new.on_complete == legacy.on_complete, state
    assert new.path_candidates == (), state
    return "equal"


@pytest.mark.parametrize("phase", [None, *sorted(PHASES)])
def test_interpreter_matches_legacy(phase: str | None) -> None:
    tally = {"equal": 0, "x5": 0, "x6": 0, "x7": 0}
    for state in _states(phase):
        tally[_assert_parity(state)] += 1
    assert tally["equal"] > 0


def test_every_exclusion_is_exercised() -> None:
    seen = {_assert_parity(state) for phase in [None, *sorted(PHASES)] for state in _states(phase)}
    assert seen == {"equal", "x5", "x6", "x7"}


@pytest.mark.parametrize("phase", [None, "design", "plan", "execute", "finish"])
def test_hold_matches_legacy(phase: str | None) -> None:
    hold = HoldFact(path="work/feature-x", decision_id="D-001", shape="park", phase=phase)
    status = "in-progress" if phase in {"execute", "finish"} else "open"
    _assert_parity(RouteState(type="Feature", work_status=status, phase=phase, hold=hold))


@pytest.mark.parametrize(
    ("phase", "blocks"),
    list(product([None, "design", "plan", "execute", "finish"], ["design", "plan", "execute", "finish"])),
)
def test_dependency_gate_matches_legacy(phase: str | None, blocks: str) -> None:
    for type_, effort in (("Feature", None), ("TestGap", "small"), ("TestGap", "large"), ("Bug", "small")):
        state = RouteState(
            type=type_,
            work_status="open",
            phase=phase,
            effort=effort,
            dependency_edges=(DependencyEdge("work/dep", blocks=blocks, needs="resolved"),),
            dependency_facts=(DependencyFact("work/dep", known=True, terminal=False, phase="plan", status="open"),),
        )
        # The old ordinal gate included stages the item never walks. The
        # configurable path deliberately drops those edges; all other cases
        # retain the legacy contract, including off-path phase refusal.
        skipped = {
            ("Feature", None): (),
            ("TestGap", "small"): ("design", "plan"),
            ("TestGap", "large"): ("design",),
            ("Bug", "small"): ("plan",),
        }[(type_, effort)]
        if blocks in skipped and phase not in skipped:
            assert route(state) == route(dataclasses.replace(state, dependency_edges=(), dependency_facts=()))
        else:
            _assert_parity(state)


def test_validation_matches_legacy() -> None:
    for state in (
        RouteState(type="Story", work_status="open"),
        RouteState(type="Bug", work_status="bogus", phase="review", effort="huge"),
        RouteState(type="Bug", work_status="open", dependency_issues=(DependencyIssue(0, "invalid-path", "bad", "x"),)),
    ):
        _assert_parity(state)

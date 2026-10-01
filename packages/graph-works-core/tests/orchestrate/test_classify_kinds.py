"""Blocked nodes are classified by kind, with parity for existing route prose."""

from __future__ import annotations

from typing import get_args

import pytest
from graph_works_core.orchestrate import commands as orchestrate
from test_orchestrate_plan import _item
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.workflow import BlockerKind, RouteState, route, state_for


def _legacy_classify(reason: str) -> str:
    """The previous prose classifier, retained as a compatibility oracle."""
    if reason.startswith("blocked on dependencies"):
        return "deps"
    if reason.startswith("effort required"):
        return "effort-required"
    if reason.startswith("open decision"):
        return "decisions"
    if "never dispatches" in reason or "human-owned" in reason:
        return "human"
    return "invalid"


def test_the_mapping_is_total_over_blocker_kinds() -> None:
    assert set(orchestrate.BLOCKER_KIND_TO_BLOCKED) == set(get_args(BlockerKind))
    assert set(orchestrate.BLOCKER_KIND_TO_BLOCKED.values()) <= orchestrate.BLOCKED_KINDS


def test_no_blocker_is_invalid() -> None:
    assert orchestrate._classify(None) == "invalid"


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(RouteState(type="Nope", work_status="open", phase="design"), id="invalid"),
        pytest.param(RouteState(type="Feature", work_status="resolved", phase="done"), id="done"),
        pytest.param(RouteState(type="Feature", work_status="wontfix", phase="design"), id="terminal"),
        pytest.param(
            RouteState(type="Feature", work_status="mitigated", phase="execute", effort="medium"),
            id="terminal-mitigated",
        ),
        pytest.param(RouteState(type="TestGap", work_status="open"), id="effort-required"),
        pytest.param(
            RouteState(
                type="Feature",
                work_status="open",
                phase="design",
                effort="medium",
                hold=HoldFact(path="work/feature-a", decision_id="D-001", shape="question", phase=None),
            ),
            id="hold",
        ),
        pytest.param(RouteState(type="Bug", work_status="open", phase="plan", effort="small"), id="phase-off-path"),
        pytest.param(
            RouteState(type="Epic", work_status="open", phase="execute", effort="large"),
            id="no-children-or-waiting",
        ),
    ],
)
def test_kind_mapping_matches_the_prose_classifier(state: RouteState) -> None:
    result = route(state)
    assert result.blockers, "each fixture must block"
    blocker = result.blockers[0]
    assert orchestrate._classify(blocker.kind) == _legacy_classify(blocker.message), blocker


def test_dependency_kind_matches_the_prose_classifier() -> None:
    path = "work/feature-a"
    dependency = "work/feature-b"
    items = (
        _item(path, dependency_edges=(DependencyEdge(dependency, blocks="plan", needs="resolved"),)),
        _item(dependency, opened="2026-08-02"),
    )
    state = state_for(items, path)
    assert state is not None
    blocker = route(state).blockers[0]
    assert blocker.kind == "dependencies"
    assert orchestrate._classify(blocker.kind) == _legacy_classify(blocker.message) == "deps"


def test_attribute_required_is_human_owned() -> None:
    assert orchestrate._classify("attribute-required") == "human"


def test_finish_incomplete_is_invalid() -> None:
    assert orchestrate._classify("finish-incomplete") == "invalid"

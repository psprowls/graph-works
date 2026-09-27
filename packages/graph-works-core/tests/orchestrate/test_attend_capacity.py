"""Attended stages draw from `max_attend`, separate from `max_parallel`."""

from __future__ import annotations

from unittest import mock

import pytest
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.workspace.dispatch import DispatchRule, RuleOrigin
from graph_works_core.workspace.errors import WorkspaceError
from test_orchestrate_plan import _ANY_TIP, _item  # same helpers the plan suite uses

ROOT = "work/epic-attend"


def _plan(items, **overrides):
    kwargs: dict[str, object] = {
        "dispatch_rules": (),
        "max_parallel": 4,
        "max_attend": 1,
        "live": (),
        "worktree_inventory": {"epic/root": "/wt/root"},
        "worktree_exists": {"/wt/root": True},
        "workspace": "/ws",
        "default_base": "main",
        "branch_tips": _ANY_TIP,
    }
    kwargs.update(overrides)
    return orchestrate.plan(items, ROOT, **kwargs)  # type: ignore[arg-type]


def _child(name: str, **overrides: object):
    return _item(f"{ROOT}/children/{name}", affects=(f"packages/{name}",), **overrides)


def _epic(*children):
    return _item(
        ROOT,
        type="Epic",
        phase="execute",
        affects=("packages/root",),
        worktree="/wt/root",
        branch="epic/root",
        child_paths=tuple(child.path for child in children),
    )


def _design(name: str, **overrides: object):
    # No spec -> `exploration` -> packaged mode `attend`.
    return _child(name, phase="design", **{"has_design_artifact": False, **overrides})


def _planning(name: str, **overrides: object):
    # `single` -> packaged mode `autonomous`.
    return _child(name, phase="plan", **overrides)


def _kinds(result):
    return {(blocked.path, blocked.kind) for blocked in result.blocked}


def _reason(result, path):
    return next(blocked.reason for blocked in result.blocked if blocked.path == path)


def _rule(match: dict[str, object], fields: dict[str, object]) -> DispatchRule:
    return DispatchRule(match=match, fields=fields, origin=RuleOrigin("test", 0, "t"))  # type: ignore[arg-type]


def test_three_ready_design_candidates_dispatch_exactly_one() -> None:
    a, b, c = _design("a"), _design("b"), _design("c")
    result = _plan((_epic(a, b, c), a, b, c))
    assert len(result.dispatches) == 1
    assert result.dispatches[0].mode == "attend"
    blocked = [blocked for blocked in result.blocked if blocked.kind == "capacity"]
    assert len(blocked) == 2
    for entry in blocked:
        assert "attend" in entry.reason
        assert "1" in entry.reason
    assert result.max_attend == 1
    assert result.attend_slots_free == 1
    assert result.slots_free == 4


def test_pools_do_not_block_each_other() -> None:
    designs = [_design(n) for n in ("a", "b")]
    plans = [_planning(n) for n in ("p", "q", "r", "s", "t")]
    result = _plan((_epic(*designs, *plans), *designs, *plans))
    modes = sorted(dispatch.mode for dispatch in result.dispatches)
    assert modes == ["attend", "autonomous", "autonomous", "autonomous", "autonomous"]
    capacity = [blocked for blocked in result.blocked if blocked.kind == "capacity"]
    assert len(capacity) == 2
    reasons = sorted(blocked.reason for blocked in capacity)
    assert sum("attend" in r for r in reasons) == 1
    assert "ready, but no worker slot free" in reasons


def test_live_design_key_counts_against_max_attend_only() -> None:
    running, waiting = _design("a"), _design("b")
    plans = [_planning(n) for n in ("p", "q")]
    items = (_epic(running, waiting, *plans), running, waiting, *plans)
    live = (orchestrate.session_name(running.path, "Feature", "design"),)
    result = _plan(items, live=live, max_parallel=2)
    assert result.attend_slots_free == 0
    assert result.slots_free == 2
    assert (waiting.path, "capacity") in _kinds(result)
    assert sorted(dispatch.slug for dispatch in result.dispatches) == sorted(p.path for p in plans)


def test_live_non_design_keys_count_against_max_parallel_only() -> None:
    execs = [_child(n, phase="execute", has_plan_artifact=True) for n in ("x", "y", "z")]
    finish = _child("f", phase="finish")
    design = _design("a")
    items = (_epic(*execs, finish, design), *execs, finish, design)
    live = (
        orchestrate.session_name(execs[0].path, "Feature", "execute"),
        orchestrate.session_name(execs[1].path, "Feature", "plan"),
        orchestrate.session_name(finish.path, "Feature", "finish"),
    )
    result = _plan(
        items, live=live, max_parallel=3, dispatch_rules=(_rule({"variant": "branch"}, {"prompt_tail": "decide"}),)
    )
    assert result.slots_free == 0
    assert result.attend_slots_free == 1
    assert [dispatch.slug for dispatch in result.dispatches] == [design.path]


def test_live_design_key_with_spec_is_still_attend() -> None:
    # The brainstorm wrote its spec but still waits on the human: the routed
    # variant would now be `reconcile` (autonomous). It must still count.
    running = _design("a", has_design_artifact=True)
    waiting = _design("b")
    items = (_epic(running, waiting), running, waiting)
    live = (orchestrate.session_name(running.path, "Feature", "design"),)
    result = _plan(items, live=live)
    assert result.attend_slots_free == 0
    assert (waiting.path, "capacity") in _kinds(result)


@pytest.mark.parametrize(
    ("phase", "artifact", "rules"),
    [
        ("design", "has_design_artifact", (_rule({"stage": "design", "has_spec": True}, {"mode": "autonomous"}),)),
        ("plan", "has_plan_artifact", (_rule({"stage": "plan", "has_plan": False}, {"mode": "attend"}),)),
    ],
)
def test_live_attend_keeps_slot_after_artifact_appears(phase, artifact, rules) -> None:
    running = _child("a", phase=phase, **{artifact: False})
    first = _plan((_epic(running), running), dispatch_rules=rules)
    assert [(dispatch.slug, dispatch.mode) for dispatch in first.dispatches] == [(running.path, "attend")]

    waiting = _child("b", phase=phase, **{artifact: False})
    current = _child("c", phase=phase, **{artifact: True})
    running_after = _child("a", phase=phase, **{artifact: True})
    live = (orchestrate.session_name(running.path, "Feature", phase),)
    second = _plan(
        (_epic(running_after, waiting, current), running_after, waiting, current),
        dispatch_rules=rules,
        live=live,
    )
    assert second.attend_slots_free == 0
    assert second.slots_free == 4
    assert (waiting.path, "capacity") in _kinds(second)
    assert [(dispatch.slug, dispatch.mode) for dispatch in second.dispatches] == [(current.path, "autonomous")]
    assert second.warnings == ()


def test_custom_rule_moves_plan_stage_into_attend_pool() -> None:
    rules = (_rule({"variant": "single"}, {"mode": "attend"}),)
    running, p, q = _planning("a"), _planning("p"), _planning("q")
    items = (_epic(running, p, q), running, p, q)
    live = (orchestrate.session_name(running.path, "Feature", "plan"),)
    result = _plan(items, live=live, dispatch_rules=rules)
    assert result.attend_slots_free == 0
    assert result.slots_free == 4
    assert result.dispatches == ()
    assert _kinds(result) == {(p.path, "capacity"), (q.path, "capacity")}


def test_unresolvable_live_profile_counts_as_attend_and_warns() -> None:
    running, waiting = _planning("a"), _design("b")
    items = (_epic(running, waiting), running, waiting)
    key = orchestrate.session_name(running.path, "Feature", "plan")
    real = orchestrate.resolve_dispatch

    def resolve(attributes, *, rules):
        if attributes["variant"] in ("single", "decompose") and attributes["stage"] == "plan":
            raise orchestrate.DispatchProfileError("broken")
        return real(attributes, rules=rules)

    with mock.patch.object(orchestrate, "resolve_dispatch", side_effect=resolve):
        result = _plan(items, live=(key,))
    assert result.attend_slots_free == 0
    assert (waiting.path, "capacity") in _kinds(result)
    assert f"live key {key}: dispatch profile unresolvable; counted against max_attend" in result.warnings


def test_live_state_classification_error_counts_as_attend_and_warns() -> None:
    running, waiting = _planning("a"), _design("b")
    items = (_epic(running, waiting), running, waiting)
    key = orchestrate.session_name(running.path, "Feature", "plan")
    real = orchestrate.state_for
    running_calls = 0

    def state_for(items, path, **kwargs):
        if path == running.path:
            nonlocal running_calls
            running_calls += 1
            if running_calls == 2:
                raise WorkspaceError("broken state")
        return real(items, path, **kwargs)

    with mock.patch.object(orchestrate, "state_for", side_effect=state_for):
        result = _plan(items, live=(key,))
    assert result.attend_slots_free == 0
    assert (waiting.path, "capacity") in _kinds(result)
    assert f"live key {key}: dispatch profile unresolvable; counted against max_attend" in result.warnings


def test_attend_candidate_refused_after_capacity_consumes_no_attend_slot() -> None:
    # Design is in READ_ONLY_PHASES, so placement refuses through `_reader_source`,
    # which runs after the capacity check.
    first, second = _design("a"), _design("b")
    items = (_epic(first, second), first, second)
    real = orchestrate._reader_source

    def source(item, **kwargs):
        if item.path == first.path:
            return orchestrate._Refusal("worktree-unprovable", "test refusal")
        return real(item, **kwargs)

    with mock.patch.object(orchestrate, "_reader_source", side_effect=source):
        result = _plan(items)
    assert [dispatch.slug for dispatch in result.dispatches] == [second.path]
    assert (first.path, "worktree-unprovable") in _kinds(result)


def test_max_attend_zero_blocks_every_attend_candidate_only() -> None:
    design, planning = _design("a"), _planning("p")
    result = _plan((_epic(design, planning), design, planning), max_attend=0)
    assert [dispatch.slug for dispatch in result.dispatches] == [planning.path]
    assert _kinds(result) == {(design.path, "capacity")}
    assert "attend" in _reason(result, design.path)


def test_broken_profile_outranks_capacity() -> None:
    first, second = _planning("p"), _planning("q")
    items = (_epic(first, second), first, second)
    rules = (_rule({"variant": "single"}, {"model": None, "reasoning_effort": "high"}),)
    result = _plan(items, max_parallel=0, dispatch_rules=rules)
    assert {kind for _, kind in _kinds(result)} == {"invalid"}


def test_terminal_root_reports_zero_attend_slots() -> None:
    root = _item(ROOT, type="Epic", phase="done", work_status="resolved")
    result = _plan((root,))
    assert result.terminal
    assert result.attend_slots_free == 0
    assert result.max_attend == 1

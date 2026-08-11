import itertools

from work_tracker_okf.hierarchy import ChildRollup
from work_tracker_okf.vocabulary import EFFORTS, PHASES, TYPES, WORKFLOW_STATUSES
from work_tracker_okf.workflow import (
    PLAN_OR_EXECUTE,
    Dispatch,
    RouteState,
    route,
)

#: Epic §2.2's seven stage/variant pairs, written out rather than derived.
SEVEN_PAIRS = {
    ("design", "exploration"),
    ("design", "diagnosis"),
    ("plan", "decompose"),
    ("plan", "single"),
    ("execute", "planned"),
    ("execute", "unplanned"),
    ("finish", "branch"),
}


def _state(**overrides) -> RouteState:
    base = {"type": "Feature", "workflow_status": "open"}
    return RouteState(**{**base, **overrides})


def _sweep():
    """Every (type, workflow_status, phase, effort) combination, plus the
    None-valued phase and effort the enums do not carry."""
    phases = [None, *sorted(PHASES)]
    efforts = [None, *sorted(EFFORTS)]
    return itertools.product(sorted(TYPES), sorted(WORKFLOW_STATUSES), phases, efforts)


def test_route_never_raises_and_always_answers():
    for type_, status, phase, effort in _sweep():
        result = route(_state(type=type_, workflow_status=status, phase=phase, effort=effort))
        assert result.dispatch is not None or result.blockers or result.on_complete is not None, (
            type_,
            status,
            phase,
            effort,
        )


def test_the_table_produces_exactly_seven_distinct_pairs():
    seen = set()
    for type_, status, phase, effort in _sweep():
        for has_plan in (False, True):
            result = route(
                _state(type=type_, workflow_status=status, phase=phase, effort=effort, has_plan_doc=has_plan)
            )
            if result.dispatch is not None:
                seen.add((result.dispatch.stage, result.dispatch.variant))
    assert seen == SEVEN_PAIRS


def test_the_sentinel_phase_is_not_a_real_phase():
    """Third guard of three (spec 3.6): even with both `advance` guards gone,
    the schema would reject the value."""
    assert PLAN_OR_EXECUTE not in PHASES


def test_an_invalid_field_blocks_and_names_itself():
    result = route(_state(type="Widget", workflow_status="nope", phase="nowhere", effort="huge"))
    assert result.dispatch is None
    assert len(result.blockers) == 4
    assert any("Widget" in blocker for blocker in result.blockers)


def test_validation_runs_before_the_phase_branches():
    """A malformed page reports what is malformed rather than falling into a
    branch that would report something else."""
    result = route(_state(type="Widget", phase="done"))
    assert any("Widget" in blocker for blocker in result.blockers)


def test_a_done_item_has_nothing_to_dispatch():
    result = route(_state(phase="done"))
    assert result.dispatch is None
    assert "nothing to dispatch" in result.blockers[0]


def test_a_terminal_status_never_dispatches():
    for status in ("resolved", "wontfix", "superseded", "mitigated"):
        result = route(_state(workflow_status=status, phase="execute"))
        assert result.dispatch is None, status
        assert "never dispatches" in result.blockers[0]


def test_the_dependency_gate_runs_after_the_terminal_checks():
    """A resolved item blocked on an unfinished dep reports 'resolved', not
    'blocked on dependencies'."""
    result = route(_state(workflow_status="resolved", phase="execute", unmet_deps=("dep",)))
    assert "never dispatches" in result.blockers[0]


def test_unmet_dependencies_block_and_are_named():
    result = route(_state(phase="plan", unmet_deps=("dep-a", "dep-b")))
    assert result.dispatch is None
    assert "dep-a, dep-b" in result.blockers[0]


def test_entry_with_a_non_open_status_is_a_human_decision():
    result = route(_state(workflow_status="accepted", phase=None))
    assert result.dispatch is None
    assert result.blockers


def test_entry_routes_a_bug_to_diagnosis_and_everything_else_to_exploration():
    assert route(_state(type="Bug")).dispatch == Dispatch("design", "diagnosis")
    for type_ in ("Feature", "Epic", "Spike", "TechDebt"):
        assert route(_state(type=type_)).dispatch == Dispatch("design", "exploration"), type_


def test_entry_sets_the_design_phase_on_dispatch():
    result = route(_state(type="Feature"))
    assert result.on_dispatch is not None
    assert result.on_dispatch.phase == "design"


def test_an_unsized_test_gap_at_entry_asks_for_effort():
    result = route(_state(type="TestGap"))
    assert result.dispatch is None
    assert "effort required" in result.blockers[0]


def test_a_small_test_gap_at_entry_skips_straight_to_execute():
    result = route(_state(type="TestGap", effort="small"))
    assert result.dispatch == Dispatch("execute", "unplanned")
    assert result.on_dispatch is not None
    assert result.on_dispatch.phase == "execute"
    assert result.on_dispatch.workflow_status == "in-progress"
    assert result.on_dispatch.requires == ("owner",)
    assert result.on_dispatch.document_status == "stable"
    assert result.on_complete is not None
    assert result.on_complete.phase == "finish"


def test_a_medium_test_gap_at_entry_plans_first():
    result = route(_state(type="TestGap", effort="medium"))
    assert result.dispatch == Dispatch("plan", "single")
    assert result.on_dispatch is not None
    assert result.on_dispatch.phase == "plan"
    assert result.on_dispatch.document_status == "stable"
    assert result.on_complete is not None
    assert result.on_complete.stamp_source == "plan"
    assert result.on_complete.sync_plan_table is True


def test_the_design_complete_fork_asks_for_effort_on_bug_like_work():
    for type_ in ("Bug", "TechDebt", "TestGap"):
        result = route(_state(type=type_, phase="design"))
        assert result.on_complete is not None
        assert result.on_complete.phase == PLAN_OR_EXECUTE, type_
        assert result.on_complete.requires == ("effort",)


def test_small_bug_like_work_skips_planning_and_larger_does_not():
    small = route(_state(type="Bug", phase="design", effort="xtra-small")).on_complete
    large = route(_state(type="Bug", phase="design", effort="large")).on_complete
    assert small is not None and small.phase == "execute"
    assert large is not None and large.phase == "plan"


def test_an_epic_sized_small_still_routes_to_plan():
    """Decomposition happens at plan and is mandatory for an epic. An epic
    that skipped planning would reach execute with no children and hit the
    gate's no-children blocker."""
    result = route(_state(type="Epic", phase="design", effort="small"))
    assert result.on_complete is not None
    assert result.on_complete.phase == "plan"


def test_every_design_complete_transition_stamps_the_spec_and_stabilises():
    for type_, effort in (("Bug", None), ("Bug", "small"), ("Feature", "large")):
        transition = route(_state(type=type_, phase="design", effort=effort)).on_complete
        assert transition is not None
        assert transition.stamp_source == "design-spec", (type_, effort)
        assert transition.document_status == "stable", (type_, effort)


def test_an_epic_at_plan_decomposes_and_syncs_no_table():
    result = route(_state(type="Epic", phase="plan"))
    assert result.dispatch == Dispatch("plan", "decompose")
    assert result.on_complete is not None
    assert result.on_complete.sync_plan_table is False
    assert result.on_complete.stamp_source == "plan"


def test_anything_else_at_plan_writes_a_single_plan_and_syncs_the_table():
    result = route(_state(type="Feature", phase="plan"))
    assert result.dispatch == Dispatch("plan", "single")
    assert result.on_complete is not None
    assert result.on_complete.sync_plan_table is True


def test_execute_dispatches_planned_or_unplanned_on_the_plan_doc():
    assert route(_state(phase="execute", has_plan_doc=True)).dispatch == Dispatch("execute", "planned")
    assert route(_state(phase="execute", has_plan_doc=False)).dispatch == Dispatch("execute", "unplanned")


def test_execute_claims_an_owner_only_when_not_already_in_progress():
    fresh = route(_state(phase="execute", workflow_status="accepted"))
    assert fresh.on_dispatch is not None
    assert fresh.on_dispatch.workflow_status == "in-progress"
    assert fresh.on_dispatch.requires == ("owner",)
    running = route(_state(phase="execute", workflow_status="in-progress"))
    assert running.on_dispatch is None


def test_an_epic_at_execute_with_no_children_says_to_decompose_it():
    result = route(_state(type="Epic", phase="execute", child_rollup=ChildRollup(0, 0, ())))
    assert result.dispatch is None
    assert "no children" in result.blockers[0]


def test_the_epic_gate_withholds_the_dispatch_while_the_feature_gate_rides_requires():
    """The asymmetry is the whole point: one type cannot act while its children
    are open, the other can act but cannot finish."""
    rollup = ChildRollup(total=2, terminal=1, open_slugs=("kid",))
    epic = route(_state(type="Epic", phase="execute", child_rollup=rollup))
    assert epic.dispatch is None
    assert "1/2 terminal" in epic.blockers[0]
    assert "kid" in epic.blockers[0]

    feature = route(_state(type="Feature", phase="execute", child_rollup=rollup))
    assert feature.dispatch == Dispatch("execute", "unplanned")
    assert feature.blockers == ()
    assert feature.on_complete is not None
    assert feature.on_complete.requires == ("children-terminal",)


def test_an_epic_whose_children_are_all_terminal_is_a_satisfied_gate():
    """`blockers == () and on_complete is not None` is the shape the pipeline
    skill keys off: nothing to run, something to advance."""
    result = route(_state(type="Epic", phase="execute", child_rollup=ChildRollup(2, 2, ())))
    assert result.dispatch is None
    assert result.blockers == ()
    assert result.on_complete is not None
    assert result.on_complete.phase == "finish"


def test_a_childless_feature_carries_no_gate():
    result = route(_state(type="Feature", phase="execute", child_rollup=None))
    assert result.on_complete is not None
    assert result.on_complete.requires == ()


def test_an_epic_at_finish_is_a_satisfied_gate_to_done():
    result = route(_state(type="Epic", phase="finish"))
    assert result.dispatch is None
    assert result.blockers == ()
    assert result.on_complete is not None
    assert (result.on_complete.phase, result.on_complete.workflow_status) == ("done", "resolved")


def test_anything_else_at_finish_dispatches_the_branch_and_needs_a_ref():
    result = route(_state(type="Feature", phase="finish"))
    assert result.dispatch == Dispatch("finish", "branch")
    assert result.on_complete is not None
    assert result.on_complete.requires == ("resolved_in",)


def test_the_finish_gate_stacks_resolved_in_and_the_children_requirement():
    rollup = ChildRollup(total=1, terminal=0, open_slugs=("kid",))
    result = route(_state(type="Feature", phase="finish", child_rollup=rollup))
    assert result.on_complete is not None
    assert result.on_complete.requires == ("resolved_in", "children-terminal")

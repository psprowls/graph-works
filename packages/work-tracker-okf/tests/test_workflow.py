import dataclasses
import itertools

import pytest
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.dependencies import DependencyEdge, DependencyFact, DependencyIssue
from work_tracker_okf.hierarchy import ChildRollup
from work_tracker_okf.vocabulary import EFFORTS, PHASES, TYPES, WORK_STATUSES
from work_tracker_okf.workflow import (
    PLAN_OR_EXECUTE,
    Dispatch,
    RouteState,
    Transition,
    hold_blocker,
    route,
)

#: Epic §2.2's stage/variant pairs, written out rather than derived. Nine since
#: `epic-design` joined the design stage — Epic and Release with no spec doc get
#: their own design skill rather than sharing `exploration` with Features.
NINE_PAIRS = {
    ("design", "exploration"),
    ("design", "diagnosis"),
    ("design", "reconcile"),
    ("design", "epic-design"),
    ("plan", "decompose"),
    ("plan", "single"),
    ("execute", "planned"),
    ("execute", "unplanned"),
    ("finish", "branch"),
}

#: One routable state per phase slot, each of which dispatches when unheld.
_PHASE_STATES = {
    "entry": {"type": "Feature", "work_status": "open", "phase": None},
    "design": {"type": "Feature", "work_status": "open", "phase": "design"},
    "plan": {"type": "Feature", "work_status": "open", "phase": "plan"},
    "execute": {"type": "Feature", "work_status": "accepted", "phase": "execute"},
    "finish": {"type": "Feature", "work_status": "in-progress", "phase": "finish"},
}


def _state(**overrides) -> RouteState:
    base = {"type": "Feature", "work_status": "open"}
    return RouteState(**{**base, **overrides})


def state_with_edge(
    *,
    phase: str | None,
    type: str = "Feature",
    effort: str | None = "medium",
    work_status: str = "open",
    blocks: str,
    needs: str = "resolved",
    dependency_phase: str | None = "design",
) -> RouteState:
    edge = DependencyEdge("dep", blocks=blocks, needs=needs)
    fact = DependencyFact(
        "dep",
        known=True,
        terminal=False,
        phase=dependency_phase,
        status="in-progress",
    )
    return RouteState(
        type=type,
        work_status=work_status,
        phase=phase,
        effort=effort,
        dependency_edges=(edge,),
        dependency_facts=(fact,),
    )


def _sweep():
    """Every (type, work_status, phase, effort) combination, plus the
    None-valued phase and effort the enums do not carry."""
    phases = [None, *sorted(PHASES)]
    efforts = [None, *sorted(EFFORTS)]
    return itertools.product(sorted(TYPES), sorted(WORK_STATUSES), phases, efforts)


def test_route_never_raises_and_always_answers():
    for type_, status, phase, effort in _sweep():
        result = route(_state(type=type_, work_status=status, phase=phase, effort=effort))
        assert result.dispatch is not None or result.blockers or result.on_complete is not None, (
            type_,
            status,
            phase,
            effort,
        )


def test_the_table_produces_exactly_nine_distinct_pairs():
    seen = set()
    for type_, status, phase, effort in _sweep():
        for has_plan in (False, True):
            for has_spec in (False, True):
                result = route(
                    _state(
                        type=type_,
                        work_status=status,
                        phase=phase,
                        effort=effort,
                        has_plan_doc=has_plan,
                        has_spec_doc=has_spec,
                    )
                )
                if result.dispatch is not None:
                    seen.add((result.dispatch.stage, result.dispatch.variant))
    assert seen == NINE_PAIRS


def test_the_sentinel_phase_is_not_a_real_phase():
    """Third guard of three (spec 3.6): even with both `advance` guards gone,
    the schema would reject the value."""
    assert PLAN_OR_EXECUTE not in PHASES


@pytest.mark.parametrize("phase", ["design", "plan", "execute", "finish"])
def test_each_branch_checks_its_own_phase(phase: str) -> None:
    state = state_with_edge(phase=phase, blocks=phase, needs="resolved", dependency_phase="design")
    result = route(state)
    assert result.dispatch is None
    assert result.reason == f"blocked on dependencies ({phase})"
    assert f"for {phase}" in result.blockers[0]


def test_phase_less_feature_checks_design() -> None:
    result = route(state_with_edge(phase=None, type="Feature", blocks="design", needs="resolved"))
    assert result.reason == "blocked on dependencies (design)"


def test_sized_test_gap_checks_the_phase_selected_by_effort() -> None:
    small = route(state_with_edge(phase=None, type="TestGap", effort="small", blocks="execute"))
    medium = route(state_with_edge(phase=None, type="TestGap", effort="medium", blocks="plan"))
    assert small.reason == "blocked on dependencies (execute)"
    assert medium.reason == "blocked on dependencies (plan)"


def test_unsized_test_gap_reports_effort_before_a_dependency_gate() -> None:
    result = route(state_with_edge(phase=None, type="TestGap", effort=None, blocks="design"))
    assert result.reason == "test-gap entry forks on effort"


def test_dependency_issues_block_routing_before_any_phase_dispatch() -> None:
    result = route(
        _state(
            phase="design",
            dependency_issues=(DependencyIssue(0, "invalid-blocks", "blocks 'build' is invalid", {}),),
        )
    )
    assert result.reason == "invalid item"
    assert "invalid-blocks" in result.blockers[0]


def test_an_invalid_field_blocks_and_names_itself():
    result = route(_state(type="Widget", work_status="nope", phase="nowhere", effort="huge"))
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
        result = route(_state(work_status=status, phase="execute"))
        assert result.dispatch is None, status
        assert "never dispatches" in result.blockers[0]


def test_the_dependency_gate_runs_after_the_terminal_checks():
    """A resolved item blocked on an unfinished dep reports 'resolved', not
    'blocked on dependencies'."""
    result = route(state_with_edge(phase="execute", work_status="resolved", blocks="execute", needs="resolved"))
    assert "never dispatches" in result.blockers[0]


def test_unmet_dependencies_block_and_are_named():
    edges = (
        DependencyEdge("work/dep-a", blocks="plan", needs="resolved"),
        DependencyEdge("work/dep-b", blocks="plan", needs="resolved"),
    )
    facts = (
        DependencyFact("work/dep-a", known=False, terminal=False),
        DependencyFact("work/dep-b", known=False, terminal=False),
    )
    result = route(_state(phase="plan", dependency_edges=edges, dependency_facts=facts))
    assert result.dispatch is None
    assert "dep-a" in result.blockers[0]
    assert "dep-b" in result.blockers[0]


def test_entry_with_a_non_open_status_is_a_human_decision():
    result = route(_state(work_status="accepted", phase=None))
    assert result.dispatch is None
    assert result.blockers


def test_entry_routes_a_bug_to_diagnosis_an_epic_to_epic_design_and_the_rest_to_exploration():
    assert route(_state(type="Bug")).dispatch == Dispatch("design", "diagnosis")
    for type_ in ("Epic", "Release"):
        assert route(_state(type=type_)).dispatch == Dispatch("design", "epic-design"), type_
    for type_ in ("Feature", "Spike", "TechDebt"):
        assert route(_state(type=type_)).dispatch == Dispatch("design", "exploration"), type_


def test_entry_with_a_pre_seeded_spec_reconciles_instead_of_restarting():
    """A pre-seeded spec (e.g. filed from a template) means design has
    something to reconcile against, not a blank page to brainstorm from --
    and this beats the diagnosis/exploration split for every type."""
    for type_ in ("Bug", "Feature"):
        result = route(_state(type=type_, has_spec_doc=True))
        assert result.dispatch == Dispatch("design", "reconcile"), type_


def test_design_reentry_with_an_existing_spec_reconciles_rather_than_restarts():
    result = route(_state(type="Feature", phase="design", has_spec_doc=True))
    assert result.dispatch == Dispatch("design", "reconcile")


def test_design_reentry_without_a_spec_still_brainstorms_or_diagnoses():
    assert route(_state(type="Bug", phase="design")).dispatch == Dispatch("design", "diagnosis")
    assert route(_state(type="Feature", phase="design")).dispatch == Dispatch("design", "exploration")


def test_design_reentry_for_an_epic_without_a_spec_uses_epic_design():
    """An Epic or Release owns the whole design stage through its own skill:
    the child index it produces is thin by construction, which is the shape
    `planning-epics` consumes at the plan stage."""
    for type_ in ("Epic", "Release"):
        assert route(_state(type=type_, phase="design")).dispatch == Dispatch("design", "epic-design"), type_


def test_a_pre_seeded_spec_beats_epic_design_for_an_epic():
    """`reconcile` keeps precedence (D-002): an Epic filed from a template has
    something to reconcile against, not a blank page. The `has_spec_doc` check
    runs BEFORE the type check in `_design_variant`, deliberately."""
    for type_ in ("Epic", "Release"):
        assert route(_state(type=type_, has_spec_doc=True)).dispatch == Dispatch("design", "reconcile"), type_
        assert route(_state(type=type_, phase="design", has_spec_doc=True)).dispatch == Dispatch(
            "design", "reconcile"
        ), type_


@pytest.mark.parametrize("slot", sorted(_PHASE_STATES))
@pytest.mark.parametrize("shape", ["question", "park", "skip"])
def test_a_held_item_never_dispatches_at_any_phase(slot: str, shape: str) -> None:
    unheld = route(RouteState(**_PHASE_STATES[slot]))
    assert unheld.dispatch is not None  # the pin: every slot dispatches when unheld
    hold = HoldFact("work/feature-a", "D-007", shape, None if shape == "question" else slot)
    result = route(RouteState(**_PHASE_STATES[slot], hold=hold))
    assert result.dispatch is None
    assert (result.on_dispatch, result.on_complete, result.on_return, result.repair) == (None, None, None, None)
    assert result.reason == f"open decision D-007 holds this item ({shape} at {slot})"
    assert result.blockers == (hold_blocker(hold),)
    assert result.blockers[0] == (
        f"open decision D-007 ({shape}) holds work/feature-a: answer via "
        "`gw work decision answer work/feature-a D-007 --answer ...`, then re-run"
    )


def test_a_held_epic_at_a_satisfied_execute_gate_offers_no_completion() -> None:
    rollup = ChildRollup(total=1, terminal=1, open_paths=())
    satisfied = _state(type="Epic", work_status="accepted", phase="execute", child_rollup=rollup)
    assert route(satisfied).on_complete is not None
    held = route(
        _state(
            type="Epic",
            work_status="accepted",
            phase="execute",
            child_rollup=rollup,
            hold=HoldFact("work/epic-a", "D-001", "skip", "execute"),
        )
    )
    assert held.on_complete is None and held.blockers


def test_the_hold_beats_a_dependency_blocker() -> None:
    state = state_with_edge(phase="design", blocks="design")
    assert route(state).reason.startswith("blocked on dependencies")
    held = route(dataclasses.replace(state, hold=HoldFact("work/x", "D-002", "question", None)))
    assert held.reason.startswith("open decision D-002")


def test_validation_and_terminal_checks_still_run_before_the_hold() -> None:
    hold = HoldFact("work/x", "D-001", "skip", "execute")
    assert route(_state(type="Nope", hold=hold)).reason == "invalid item"
    assert route(_state(work_status="resolved", phase="done", hold=hold)).reason == "pipeline complete"
    assert route(_state(work_status="wontfix", phase="execute", hold=hold)).reason == "disposition is human-owned"


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
    assert result.on_dispatch.work_status == "in-progress"
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
        assert transition.stamp_source == "design", (type_, effort)
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
    fresh = route(_state(phase="execute", work_status="accepted"))
    assert fresh.on_dispatch is not None
    assert fresh.on_dispatch.work_status == "in-progress"
    assert fresh.on_dispatch.requires == ("owner",)
    running = route(_state(phase="execute", work_status="in-progress"))
    assert running.on_dispatch is None


def test_an_epic_at_execute_with_no_children_says_to_decompose_it():
    result = route(_state(type="Epic", phase="execute", child_rollup=ChildRollup(0, 0, ())))
    assert result.dispatch is None
    assert "no children" in result.blockers[0]


def test_the_epic_gate_withholds_the_dispatch_while_the_feature_gate_rides_requires():
    """The asymmetry is the whole point: one type cannot act while its children
    are open, the other can act but cannot finish."""
    rollup = ChildRollup(total=2, terminal=1, open_paths=("work/kid",))
    epic = route(_state(type="Epic", phase="execute", child_rollup=rollup, open_descendants=("work/kid",)))
    assert epic.dispatch is None
    assert "1/2 terminal" in epic.blockers[0]
    assert "kid" in epic.blockers[0]

    feature = route(_state(type="Feature", phase="execute", child_rollup=rollup, open_descendants=("work/kid",)))
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


def test_the_epic_gate_reads_open_descendants_not_the_direct_rollup():
    """Axis 2: a terminal direct child can still hold an open grandchild. The
    rollup alone would call this satisfied; `open_descendants` must not."""
    rollup = ChildRollup(total=1, terminal=1, open_paths=())
    result = route(
        _state(type="Epic", phase="execute", child_rollup=rollup, open_descendants=("work/feat/children/gc",))
    )
    assert result.dispatch is None
    assert result.on_complete is None
    assert any("gc" in blocker for blocker in result.blockers)


def test_a_childless_feature_carries_no_gate():
    result = route(_state(type="Feature", phase="execute", child_rollup=None))
    assert result.on_complete is not None
    assert result.on_complete.requires == ()


def test_an_epic_at_finish_is_a_satisfied_gate_to_done():
    result = route(_state(type="Epic", phase="finish"))
    assert result.dispatch is None
    assert result.blockers == ()
    assert result.on_complete is not None
    assert (result.on_complete.phase, result.on_complete.work_status) == ("done", "resolved")


def test_anything_else_at_finish_dispatches_the_branch_and_needs_a_ref():
    result = route(_state(type="Feature", phase="finish"))
    assert result.dispatch == Dispatch("finish", "branch")
    assert result.on_complete is not None
    assert result.on_complete.requires == ("resolved_in",)


def test_the_finish_gate_stacks_resolved_in_and_the_children_requirement():
    rollup = ChildRollup(total=1, terminal=0, open_paths=("work/kid",))
    result = route(_state(type="Feature", phase="finish", child_rollup=rollup, open_descendants=("work/kid",)))
    assert result.on_complete is not None
    assert result.on_complete.requires == ("resolved_in", "children-terminal")


def test_an_epic_at_finish_with_a_post_finish_child_repairs_instead_of_resolving():
    """D3: `route()` never hands the coordinator a `children-open` refusal --
    it plans a return to `execute` instead, carried on the new `repair` field."""
    result = route(_state(type="Epic", phase="finish", open_descendants=("work/epic/children/late",)))
    assert result.on_complete is None
    assert result.repair == Transition(phase="execute", work_status="in-progress")
    assert result.on_return == Transition(phase="execute", work_status="in-progress")
    assert any("late" in blocker for blocker in result.blockers)


def test_an_epic_at_finish_with_no_open_descendants_still_resolves():
    result = route(_state(type="Epic", phase="finish"))
    assert result.repair is None
    assert result.on_complete is not None
    assert (result.on_complete.phase, result.on_complete.work_status) == ("done", "resolved")


# --- branch ownership follows the stamp, not the type (D-002) -------------


@pytest.mark.parametrize("type_", ["Epic", "Release"])
def test_a_branch_stamped_parent_at_finish_dispatches_the_branch_and_needs_a_ref(type_: str) -> None:
    result = route(_state(type=type_, phase="finish", work_status="in-progress", has_branch=True))
    assert result.dispatch == Dispatch("finish", "branch")
    assert result.reason == f"{type_} at finish stage"
    assert result.blockers == ()
    assert result.repair is None
    assert result.on_complete == Transition(phase="done", work_status="resolved", requires=("resolved_in",))
    assert result.on_return == Transition(phase="execute", work_status="in-progress")


@pytest.mark.parametrize("type_", ["Epic", "Release"])
def test_an_unstamped_parent_at_finish_keeps_its_direct_resolution(type_: str) -> None:
    result = route(_state(type=type_, phase="finish", work_status="in-progress"))
    assert result.dispatch is None
    assert result.reason == f"{type_.lower()} at finish stage"
    assert result.blockers == ()
    assert result.repair is None
    assert result.on_complete == Transition(phase="done", work_status="resolved")
    assert result.on_return == Transition(phase="execute", work_status="in-progress")


@pytest.mark.parametrize("type_", ["Epic", "Release"])
@pytest.mark.parametrize("has_branch", [False, True])
@pytest.mark.parametrize("descendant", ["work/parent/children/late", "work/parent/children/done/children/late"])
def test_a_parent_reopened_at_finish_repairs_whether_or_not_it_is_stamped(
    type_: str, has_branch: bool, descendant: str
) -> None:
    result = route(
        _state(
            type=type_,
            phase="finish",
            work_status="in-progress",
            has_branch=has_branch,
            open_descendants=(descendant,),
        )
    )
    assert result.dispatch is None
    assert result.on_complete is None
    assert result.repair == Transition(phase="execute", work_status="in-progress")
    assert result.on_return == Transition(phase="execute", work_status="in-progress")
    assert any(descendant in blocker for blocker in result.blockers)


@pytest.mark.parametrize("type_", ["Feature", "Bug"])
@pytest.mark.parametrize("has_branch", [False, True])
def test_branch_ownership_does_not_change_an_ordinary_finish(type_: str, has_branch: bool) -> None:
    result = route(_state(type=type_, phase="finish", work_status="in-progress", has_branch=has_branch))
    assert result.dispatch == Dispatch("finish", "branch")
    assert result.on_complete is not None
    assert result.on_complete.requires == ("resolved_in",)


@pytest.mark.parametrize("type_", ["Epic", "Release"])
@pytest.mark.parametrize("has_branch", [False, True])
def test_a_parent_still_holds_on_a_finish_dependency_regardless_of_branch_ownership(
    type_: str, has_branch: bool
) -> None:
    state = dataclasses.replace(
        state_with_edge(phase="finish", type=type_, work_status="in-progress", blocks="finish"),
        has_branch=has_branch,
        open_descendants=("work/parent/children/late",),
    )
    result = route(state)
    assert result.dispatch is None
    assert result.on_complete is None
    assert result.repair is None
    assert result.on_return is None
    assert result.reason == "blocked on dependencies (finish)"
    assert result.blockers


@pytest.mark.parametrize("type_", ["Epic", "Release"])
@pytest.mark.parametrize("has_branch", [False, True])
@pytest.mark.parametrize("competing_gate", ["none", "dependency", "descendant"])
def test_a_held_parent_at_finish_blocks_before_other_gates_regardless_of_branch_ownership(
    type_: str, has_branch: bool, competing_gate: str
) -> None:
    hold = HoldFact("work/parent", "D-008", "skip", "finish")
    state = _state(type=type_, phase="finish", work_status="in-progress")
    if competing_gate == "dependency":
        state = state_with_edge(phase="finish", type=type_, work_status="in-progress", blocks="finish")
    state = dataclasses.replace(
        state,
        hold=hold,
        has_branch=has_branch,
        open_descendants=("work/parent/children/late",) if competing_gate == "descendant" else (),
    )
    result = route(state)
    assert result.reason == "open decision D-008 holds this item (skip at finish)"
    assert result.blockers == (hold_blocker(hold),)
    assert result.dispatch is None
    assert result.on_dispatch is None
    assert result.on_complete is None
    assert result.on_return is None
    assert result.repair is None


# --- the way home ---------------------------------------------------------


def test_the_finish_stage_offers_a_return_to_execute() -> None:
    result = route(_state(phase="finish", work_status="in-progress", effort="small"))
    assert result.on_return == Transition(phase="execute", work_status="in-progress")


def test_an_epic_at_finish_offers_the_same_return() -> None:
    result = route(_state(type="Epic", phase="finish", work_status="in-progress"))
    assert result.on_return == Transition(phase="execute", work_status="in-progress")


def test_no_stage_before_finish_offers_a_return() -> None:
    for phase in ("design", "plan", "execute"):
        assert route(_state(phase=phase, work_status="in-progress", effort="small")).on_return is None


def test_blast_radius_is_routing_neutral_and_populated_from_item():
    from work_helpers import make_item
    from work_tracker_okf.workflow import state_for

    item = make_item("feature-a", blast_radius="package")
    state = state_for([item], item.path)
    assert state.blast_radius == "package"
    assert route(_state(blast_radius="system")) == route(_state(blast_radius=None))


def test_foreign_stamp_owns_finish_branch(tmp_path):
    from okf_io import load_bundle
    from work_tracker_okf.items import load_items
    from work_tracker_okf.workflow import state_for

    (tmp_path / "work").mkdir()
    (tmp_path / "work/epic-a.md").write_text(
        "---\ntype: Epic\nwork_status: in-progress\nphase: finish\n"
        "repo_stamps:\n  ui: {worktree: /ui/epic, branch: epic/a}\n---\n",
        encoding="utf-8",
    )
    item = load_items(load_bundle(tmp_path))[0]
    assert item is not None
    state = state_for((item,), item.path)
    assert state is not None and state.has_branch
    routed = route(state)
    assert routed.dispatch is not None
    assert routed.dispatch.stage == "finish"
    assert routed.dispatch.variant == "branch"


def test_foreign_stamp_release_still_requires_release_date(tmp_path):
    from datetime import date

    from okf_io import load_bundle
    from work_tracker_okf.advance import advance
    from work_tracker_okf.items import load_items

    (tmp_path / "work").mkdir()
    (tmp_path / "work/release-a.md").write_text(
        "---\ntype: Release\nwork_status: in-progress\nphase: finish\n"
        "repo_stamps:\n  ui: {worktree: /ui/epic, branch: epic/a}\n---\n",
        encoding="utf-8",
    )
    items = load_items(load_bundle(tmp_path))
    result = advance(items, items[0].path, today=date(2026, 9, 23), resolved_in="abc1234")
    assert result.refusal is not None
    assert "released_at" in str(result)

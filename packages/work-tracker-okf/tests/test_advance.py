import difflib
from datetime import date

import pytest
from okf_io import Bundle, parse
from work_helpers import make_item
from work_tracker_okf.advance import AdvancePlan, FieldChange, advance, apply
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.items import load_items
from work_tracker_okf.workflow import PLAN_OR_EXECUTE, RouteResult, Transition

TODAY = date(2026, 8, 10)


def _plan_for(items, path, **kwargs) -> AdvancePlan:
    return advance(items, path if path.startswith("work/") else f"work/{path}", today=TODAY, **kwargs)


def _keys(plan: AdvancePlan) -> list[str]:
    return [change.key for change in plan.changes]


def test_release_finish_requires_released_at():
    item = make_item("work/release-cutover", type="Release", phase="finish", work_status="in-progress")
    plan = advance((item,), item.path, today=TODAY)
    assert plan.refusal == "released-at-required"


def test_release_finish_records_released_at_and_resolves():
    item = make_item("work/release-cutover", type="Release", phase="finish", work_status="in-progress")
    released_at = date(2026, 8, 9)
    plan = advance((item,), item.path, today=TODAY, released_at=released_at)
    assert plan.refusal is None
    assert FieldChange("work_status", "in-progress", "resolved") in plan.changes
    assert FieldChange("released_at", None, released_at) in plan.changes


# --- a stamped parent resolves only with a resolution ref (D-002) ---------

_STAMP = {"branch": "epic/integration-1a2b3c4d", "worktree": "/wt/integration"}


def _stamped(type_: str, **overrides):
    return make_item(
        "work/parent-int", type=type_, phase="finish", work_status="in-progress", **{**_STAMP, **overrides}
    )


def test_a_stamped_epic_cannot_finish_without_a_resolution_ref():
    item = _stamped("Epic")
    plan = advance((item,), item.path, today=TODAY)
    assert plan.refusal == "resolved-in-required"
    assert plan.changes == ()


def test_a_stamped_epic_resolves_with_a_resolution_ref():
    item = _stamped("Epic")
    plan = advance((item,), item.path, today=TODAY, resolved_in="abc1234")
    assert plan.refusal is None
    assert FieldChange("phase", "finish", "done") in plan.changes
    assert FieldChange("work_status", "in-progress", "resolved") in plan.changes
    assert FieldChange("resolved_in", None, "abc1234") in plan.changes


def test_an_unstamped_epic_still_resolves_with_nothing_supplied():
    item = make_item("work/parent-int", type="Epic", phase="finish", work_status="in-progress")
    plan = advance((item,), item.path, today=TODAY)
    assert plan.refusal is None
    assert FieldChange("work_status", "in-progress", "resolved") in plan.changes
    assert "resolved_in" not in _keys(plan)


def test_a_stamped_release_refuses_its_missing_date_first():
    item = _stamped("Release")
    plan = advance((item,), item.path, today=TODAY)
    assert plan.refusal == "released-at-required"
    assert plan.changes == ()


def test_a_stamped_release_with_a_date_still_needs_a_resolution_ref():
    item = _stamped("Release")
    plan = advance((item,), item.path, today=TODAY, released_at=date(2026, 8, 9))
    assert plan.refusal == "resolved-in-required"
    assert plan.changes == ()


def test_a_stamped_release_resolves_with_its_date_and_a_resolution_ref():
    item = _stamped("Release")
    released_at = date(2026, 8, 9)
    plan = advance((item,), item.path, today=TODAY, released_at=released_at, resolved_in="abc1234")
    assert plan.refusal is None
    assert FieldChange("phase", "finish", "done") in plan.changes
    assert FieldChange("work_status", "in-progress", "resolved") in plan.changes
    assert FieldChange("released_at", None, released_at) in plan.changes
    assert FieldChange("resolved_in", None, "abc1234") in plan.changes


@pytest.mark.parametrize("type_", ["Epic", "Release"])
def test_a_stamped_parent_with_an_open_child_is_blocked_not_resolved(type_: str):
    child_path = "work/parent-int/children/bug-late"
    parent = _stamped(type_, active_child_paths=(child_path,))
    child = make_item(
        child_path,
        type="Bug",
        parent_path=parent.path,
        ancestor_paths=(parent.path,),
        work_status="open",
        phase="execute",
    )
    plan = advance((parent, child), parent.path, today=TODAY, resolved_in="abc1234", released_at=TODAY)
    assert plan.refusal == "blocked"
    assert child.path in plan.detail
    assert plan.changes == ()


@pytest.mark.parametrize("type_", ["Epic", "Release"])
@pytest.mark.parametrize("has_branch", [False, True])
def test_a_parent_with_an_open_grandchild_is_blocked_regardless_of_branch_ownership(type_: str, has_branch: bool):
    child_path = "work/parent-int/children/feature-done"
    grandchild_path = f"{child_path}/children/bug-late"
    parent = _stamped(type_, branch=_STAMP["branch"] if has_branch else None, active_child_paths=(child_path,))
    child = make_item(
        child_path,
        type="Feature",
        parent_path=parent.path,
        ancestor_paths=(parent.path,),
        active_child_paths=(grandchild_path,),
        work_status="resolved",
        phase="done",
    )
    grandchild = make_item(
        grandchild_path,
        type="Bug",
        parent_path=child.path,
        ancestor_paths=(parent.path, child.path),
        work_status="open",
        phase="execute",
    )
    plan = advance((parent, child, grandchild), parent.path, today=TODAY, resolved_in="abc1234", released_at=TODAY)
    assert plan.refusal == "blocked"
    assert grandchild.path in plan.detail
    assert plan.changes == ()
    assert plan.route is not None
    assert plan.route.dispatch is None
    assert plan.route.on_complete is None
    assert plan.route.repair == Transition(phase="execute", work_status="in-progress")


@pytest.mark.parametrize("type_", ["Epic", "Release"])
@pytest.mark.parametrize("has_branch", [False, True])
@pytest.mark.parametrize("return_", [False, True])
def test_a_held_parent_cannot_finish_or_return_regardless_of_branch_ownership(
    type_: str, has_branch: bool, return_: bool
):
    item = _stamped(type_, branch=_STAMP["branch"] if has_branch else None)
    hold = HoldFact(item.path, "D-009", "skip", "finish")
    plan = advance(
        (item,),
        item.path,
        today=TODAY,
        hold=hold,
        return_=return_,
        resolved_in=None if return_ else "abc1234",
        released_at=TODAY,
    )
    assert plan.refusal == "blocked"
    assert "open decision D-009 (skip)" in plan.detail
    assert plan.changes == ()


# --- refusals -------------------------------------------------------------


def test_an_unknown_path_is_refused():
    plan = _plan_for([make_item("a")], "b")
    assert plan.refusal == "unknown-path"
    assert plan.changes == ()


def test_an_unreadable_member_is_refused_with_the_reason_named():
    plan = _plan_for([make_item("a")], "b", unreadable={"work/b.md": "could not be read: [Errno 13] Permission denied"})
    assert plan.refusal == "unreadable-member"
    assert "work/b.md" in plan.detail
    assert "Permission denied" in plan.detail
    assert plan.changes == ()


def test_a_genuinely_missing_path_still_refuses_unknown_path_even_with_unreadable_present():
    plan = _plan_for(
        [make_item("a")], "b", unreadable={"work/other.md": "could not be read: [Errno 13] Permission denied"}
    )
    assert plan.refusal == "unknown-path"
    assert plan.changes == ()


def test_a_blocked_route_is_refused_with_the_blockers_as_detail():
    plan = _plan_for([make_item("a", type="Widget")], "a")
    assert plan.refusal == "blocked"
    assert "Widget" in plan.detail


@pytest.mark.parametrize(
    ("phase", "work_status", "kwargs"),
    [
        (None, "open", {}),
        ("design", "open", {}),
        ("finish", "in-progress", {"return_": True}),
    ],
)
def test_advance_refuses_a_held_item_in_every_mode(phase, work_status, kwargs) -> None:
    item = make_item("work/feature-a", type="Feature", phase=phase, work_status=work_status, effort="medium")
    assert advance((item,), item.path, today=TODAY, **kwargs).refusal is None
    hold = HoldFact(item.path, "D-003", "skip", phase or "entry")
    plan = advance((item,), item.path, today=TODAY, hold=hold, **kwargs)
    assert plan.refusal == "blocked"
    assert "open decision D-003 (skip) holds work/feature-a" in plan.detail
    assert plan.changes == ()


def test_advance_only_blocks_at_the_dependency_edge_phase() -> None:
    dependency = make_item("dep", phase="design", work_status="in-progress")
    edge = DependencyEdge("work/dep", blocks="execute", needs="resolved")
    before_gate = _plan_for([make_item("feature", phase="plan", dependency_edges=(edge,)), dependency], "feature")
    at_gate = _plan_for([make_item("feature", phase="execute", dependency_edges=(edge,)), dependency], "feature")
    assert before_gate.refusal is None
    assert at_gate.refusal == "blocked"


def test_advance_refuses_a_satisfied_but_invalid_parent_dependency() -> None:
    items = [
        make_item("parent", type="Epic", work_status="resolved"),
        make_item(
            "parent/children/child",
            parent_path="work/parent",
            ancestor_paths=("work/parent",),
            phase="execute",
            dependency_edges=(DependencyEdge("work/parent", "execute", "resolved"),),
        ),
    ]
    plan = _plan_for(items, "work/parent/children/child")
    assert plan.refusal == "blocked"
    assert "targets-parent" in plan.detail


def test_a_satisfied_gate_with_nothing_to_advance_is_still_advanceable():
    """An epic whose children are all terminal has an on_complete: it is a
    satisfied gate, not a refusal."""
    items = [
        make_item(
            "epic",
            type="Epic",
            phase="execute",
            work_status="accepted",
            active_child_paths=("work/epic/children/kid",),
        ),
        make_item(
            "epic/children/kid",
            parent_path="work/epic",
            ancestor_paths=("work/epic",),
            work_status="resolved",
        ),
    ]
    plan = _plan_for(items, "epic")
    assert plan.refusal is None
    assert plan.transition == Transition(phase="finish")


def test_a_route_with_no_transition_at_all_is_nothing_to_advance():
    """An epic at execute with no children blocks; an epic at design-with-no-
    fork does not exist -- the reachable no-transition shape is the terminal
    one, which validation lets through and the table answers with blockers.
    The synthetic case here is a design-stage dispatch whose on_complete is
    present, so use the plan-stage gate instead: an epic at execute with open
    children reports `blocked`, and only a hand-built RouteResult reaches
    `nothing-to-advance`. Assert it through the public path that can:
    a TestGap at entry with no effort."""
    plan = _plan_for([make_item("gap", type="TestGap")], "gap")
    assert plan.refusal == "blocked"
    assert "effort required" in plan.detail


def test_the_design_complete_fork_refuses_without_an_effort():
    plan = _plan_for([make_item("bug", type="Bug", phase="design")], "bug")
    assert plan.refusal == "effort-required"
    assert plan.changes == ()


def test_the_sentinel_phase_is_refused_by_its_own_guard(monkeypatch):
    """Guard two of three: even if `requires` is accidentally stripped from a
    row, the sentinel phase alone still refuses -- removing `requires`
    without also removing the phase must not open the hole."""
    stripped = RouteResult(
        dispatch=None,
        reason="synthetic: requires stripped",
        on_complete=Transition(phase=PLAN_OR_EXECUTE),  # no `requires`
    )
    monkeypatch.setattr("work_tracker_okf.advance.route", lambda state: stripped)
    plan = _plan_for([make_item("bug", type="Bug", phase="design")], "bug")
    assert plan.refusal == "effort-required"
    assert plan.changes == ()


def test_a_route_with_no_transition_at_all_refuses_nothing_to_advance(monkeypatch):
    """A RouteResult with no blockers and no dispatch/complete transition is
    unreachable through the public route() table by construction; only a
    synthetic case reaches nothing-to-advance."""
    synthetic = RouteResult(dispatch=None, reason="synthetic")
    monkeypatch.setattr("work_tracker_okf.advance.route", lambda state: synthetic)
    plan = _plan_for([make_item("bug", type="Bug", phase="design")], "bug")
    assert plan.refusal == "nothing-to-advance"
    assert plan.changes == ()


def test_an_owner_is_required_to_start_execution():
    plan = _plan_for([make_item("feat", phase="execute", work_status="accepted")], "feat")
    assert plan.refusal == "owner-required"
    assert plan.changes == ()


def test_an_owner_already_on_the_page_satisfies_the_requirement():
    items = [make_item("feat", phase="execute", work_status="accepted", owner="human:pat")]
    assert _plan_for(items, "feat").refusal is None


def test_resolved_in_is_required_to_finish():
    plan = _plan_for([make_item("feat", phase="finish", work_status="in-progress")], "feat")
    assert plan.refusal == "resolved-in-required"


def test_open_children_refuse_a_feature_finishing():
    items = [
        make_item(
            "feat",
            type="Feature",
            phase="finish",
            work_status="in-progress",
            active_child_paths=("work/feat/children/kid",),
        ),
        make_item(
            "feat/children/kid",
            parent_path="work/feat",
            ancestor_paths=("work/feat",),
            work_status="open",
        ),
    ]
    plan = _plan_for(items, "feat")
    assert plan.refusal == "children-open"
    assert "kid" in plan.detail
    assert plan.changes == ()


def test_open_grandchildren_refuse_a_parent_finishing():
    items = [
        make_item(
            "feat",
            type="Feature",
            phase="finish",
            work_status="in-progress",
            active_child_paths=("work/feat/children/child",),
        ),
        make_item(
            "feat/children/child",
            type="Feature",
            parent_path="work/feat",
            ancestor_paths=("work/feat",),
            work_status="resolved",
            active_child_paths=("work/feat/children/child/children/grandchild",),
        ),
        make_item(
            "feat/children/child/children/grandchild",
            parent_path="work/feat/children/child",
            ancestor_paths=("work/feat", "work/feat/children/child"),
            work_status="open",
        ),
    ]
    plan = _plan_for(items, "feat")
    assert plan.refusal == "children-open"
    assert "grandchild" in plan.detail
    assert plan.changes == ()


def test_every_refusal_carries_an_empty_change_list():
    """What makes `apply` inherently safe: there is nothing to write."""
    cases = [
        ([make_item("a")], "b"),
        ([make_item("a", type="Widget")], "a"),
        ([make_item("bug", type="Bug", phase="design")], "bug"),
        ([make_item("feat", phase="execute", work_status="accepted")], "feat"),
        ([make_item("feat", phase="finish", work_status="in-progress")], "feat"),
    ]
    for items, path in cases:
        plan = _plan_for(items, path)
        assert plan.refusal is not None, path
        assert plan.changes == ()
        assert plan.changed is False
        assert plan.diff()


# --- the change list ------------------------------------------------------


def test_entry_plans_the_dispatch_transition_and_stamps_updated():
    plan = _plan_for([make_item("feat", opened="2026-01-01", updated="2026-01-01")], "feat")
    assert plan.refusal is None
    assert _keys(plan) == ["phase", "updated"]
    assert plan.changes[0] == FieldChange("phase", None, "design")
    assert plan.changes[-1] == FieldChange("updated", "2026-01-01", TODAY)


def test_the_flags_are_planned_in_the_documented_key_order():
    items = [make_item("gap", type="TestGap", status="draft", updated="2026-01-01")]
    plan = _plan_for(items, "gap", effort="small", owner="human:pat")
    assert _keys(plan) == ["phase", "work_status", "status", "effort", "owner", "updated"]


def test_a_value_already_at_its_target_is_not_a_change():
    """Writing an already-stable `status` is a no-op the plan reports as no
    change."""
    items = [make_item("gap", type="TestGap", status="stable", updated="2026-01-01")]
    plan = _plan_for(items, "gap", effort="small", owner="human:pat")
    assert "status" not in _keys(plan)


def test_a_same_day_advance_leaves_updated_alone():
    items = [make_item("feat", updated=TODAY.isoformat())]
    plan = _plan_for(items, "feat")
    assert "updated" not in _keys(plan)


def test_the_stamp_and_the_table_sync_stay_unresolved_requests():
    """Child 3 imports nothing of child 2's: the sources[] stamp and the plan
    row are handed onward, not applied."""
    plan = _plan_for([make_item("feat", type="Feature", phase="plan")], "feat")
    assert plan.stamp_source == "plan"
    assert plan.sync_plan_table is True
    assert "sources" not in _keys(plan)


# --- apply ----------------------------------------------------------------


def _document(bundle: Bundle, concept_id: str):
    document = bundle.concept(concept_id)
    assert document is not None
    return document


def _diff(before: str, after: str) -> list[str]:
    return [
        line
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]


def test_apply_on_a_refusal_writes_nothing(minimal_bundle: Bundle):
    document = _document(minimal_bundle, "work/bug-gamma")
    before = document.serialize()
    plan = _plan_for(load_items(minimal_bundle), "bug-gamma")
    assert plan.refusal == "blocked"
    with pytest.raises(AssertionError):
        apply(document, plan)
    assert document.serialize() == before


def test_apply_touches_only_the_keys_the_transition_names(minimal_bundle: Bundle):
    """The acceptance property, asserted against the whole serialized diff --
    a weaker assertion would let a ruamel dialect quirk rewrite a neighbouring
    key on every advance and never be noticed."""
    items = [make_item("tech-debt-delta", type="TechDebt", updated="2026-01-01")]
    document = parse("---\ntype: TechDebt\nwork_status: open\nupdated: 2026-01-01\n---\n\nbody\n")
    before = document.serialize()
    plan = _plan_for(items, "tech-debt-delta")
    assert plan.refusal is None
    apply(document, plan)
    changed = {line[1:].split(":")[0].strip() for line in _diff(before, document.serialize())}
    assert changed == {key for key in _keys(plan)} | {"updated"} - {""}


def test_updated_is_written_bare_not_quoted(minimal_bundle: Bundle):
    """A `str` that would re-parse as a date comes back quoted; a `date` does
    not, and every authored page in this lane is bare."""
    items = [make_item("tech-debt-delta", type="TechDebt", updated="2026-01-01")]
    document = parse("---\ntype: TechDebt\nwork_status: open\nupdated: 2026-01-01\n---\n\nbody\n")
    apply(document, _plan_for(items, "tech-debt-delta"))
    assert "updated: 2026-08-10" in document.serialize()
    assert "updated: '2026-08-10'" not in document.serialize()


def test_status_is_inserted_after_tags_while_phase_appends(minimal_bundle: Bundle):
    """`status` is in okf-io's PREFERRED_KEY_ORDER; `phase` is not."""
    items = [make_item("test-gap-epsilon", type="TestGap", effort="small", status="draft")]
    document = parse("---\ntype: TestGap\ntags: [fixture]\nwork_status: open\n---\n\nbody\n")
    plan = _plan_for(items, "test-gap-epsilon", owner="human:pat")
    apply(document, plan)
    lines = [line.split(":")[0] for line in document.serialize().splitlines() if ":" in line]
    assert lines.index("status") == lines.index("tags") + 1
    assert lines.index("phase") > lines.index("status")


def test_a_design_skipping_test_gap_reaches_stable(minimal_bundle: Bundle):
    """W-D's exemption must not outlive design: the item that skipped design
    is exactly the item that would otherwise keep it forever."""
    items = [make_item("gap", type="TestGap", status="draft", effort="small")]
    document = parse("---\ntype: TestGap\nstatus: draft\n---\n\nbody\n")
    plan = _plan_for(items, "gap", owner="human:pat")
    apply(document, plan)
    assert document.fm_data()["status"] == "stable"


def test_a_page_that_fails_validation_never_reaches_document_set(minimal_bundle: Bundle):
    """`bug-broken-eta` projects with type='' and work_status='', which fails
    validation row 1. The refusal is data; the raise is unreachable."""
    items = load_items(minimal_bundle)
    plan = _plan_for(items, "bug-broken-eta")
    assert plan.refusal == "blocked"
    assert plan.changes == ()


def test_apply_raises_nothing_of_its_own_on_an_unparseable_document():
    """`Document.set` raises on a document that failed to parse -- the only
    raise anywhere on this path. A refused plan never gets there."""
    document = parse("---\n: :\nbroken\n---\n\nbody\n")
    plan = _plan_for([make_item("a")], "b")
    assert plan.refusal == "unknown-path"
    with pytest.raises(AssertionError):
        apply(document, plan)


def test_advance_stamps_worktree_and_branch_when_supplied() -> None:
    items = (make_item("x", type="Feature", work_status="open", phase="design"),)
    plan = _plan_for(items, "x", worktree="/tmp/wt/x", branch="feature/x")
    assert plan.refusal is None
    changed = {change.key: change.after for change in plan.changes}
    assert changed["worktree"] == "/tmp/wt/x"
    assert changed["branch"] == "feature/x"


def test_advance_without_provenance_keywords_changes_nothing_extra() -> None:
    items = (make_item("x", type="Feature", work_status="open", phase="design"),)
    assert "worktree" not in _keys(_plan_for(items, "x"))
    assert "branch" not in _keys(_plan_for(items, "x"))


def test_advance_does_not_rewrite_an_unchanged_worktree() -> None:
    items = (make_item("x", type="Feature", work_status="open", phase="design", worktree="/tmp/wt/x"),)
    assert "worktree" not in _keys(_plan_for(items, "x", worktree="/tmp/wt/x"))


# --- the way home ---------------------------------------------------------


def test_returning_an_item_at_finish_plans_the_way_back_to_execute():
    item = make_item("work/feature-a", phase="finish", work_status="in-progress")
    plan = advance((item,), item.path, today=TODAY, return_=True)
    assert plan.refusal is None
    assert plan.transition == Transition(phase="execute", work_status="in-progress")
    assert FieldChange("phase", "finish", "execute") in plan.changes


def test_returning_an_item_that_is_not_at_finish_is_refused():
    item = make_item("work/feature-a", phase="execute", work_status="in-progress")
    plan = advance((item,), item.path, today=TODAY, return_=True)
    assert plan.refusal == "return-not-available"
    assert plan.changes == ()


def test_returning_with_resolved_in_is_refused_rather_than_silently_ignored():
    item = make_item("work/feature-a", phase="finish", work_status="in-progress")
    plan = advance((item,), item.path, today=TODAY, return_=True, resolved_in="pr-1")
    assert plan.refusal == "return-not-available"
    assert "--resolved-in" in plan.detail


def test_returning_does_not_stamp_a_source_or_sync_a_plan_row():
    item = make_item("work/feature-a", phase="finish", work_status="in-progress")
    plan = advance((item,), item.path, today=TODAY, return_=True)
    assert plan.stamp_source is None
    assert plan.sync_plan_table is False


def test_the_refusal_vocabulary_carries_the_affects_coverage_reason() -> None:
    # The vocabulary is the CLI's rendering contract: `advance_payload` emits
    # `plan.refusal` verbatim, so a reason produced one band up must be a
    # declared member here or `mypy --strict` refuses the gate's return.
    import typing

    from work_tracker_okf.advance import RefusalReason

    assert "no-affects-touched" in typing.get_args(RefusalReason)
    assert "unreadable-member" in typing.get_args(RefusalReason)


# --- which transition the plan picked (bug-transcript-capture-labels-the-wrong-phase) ---


def test_first_dispatch_is_a_dispatch_trigger():
    item = make_item("work/bug-a", type="Bug", phase=None, work_status="open", effort="medium")
    plan = advance((item,), item.path, today=TODAY)
    assert plan.refusal is None
    assert plan.transition is not None and plan.transition.phase == "design"
    assert plan.trigger == "dispatch"


def test_stage_exit_is_a_complete_trigger():
    item = make_item("work/bug-a", type="Bug", phase="plan", work_status="open", effort="medium")
    plan = advance((item,), item.path, today=TODAY)
    assert plan.refusal is None
    assert plan.transition is not None and plan.transition.phase == "execute"
    assert plan.trigger == "complete"


def test_return_is_a_return_trigger():
    item = make_item("work/bug-a", type="Bug", phase="finish", work_status="in-progress", effort="medium")
    plan = advance((item,), item.path, today=TODAY, return_=True)
    assert plan.refusal is None
    assert plan.trigger == "return"


def test_a_refusal_carries_no_trigger():
    plan = advance((), "work/nope", today=TODAY)
    assert plan.refusal == "unknown-path"
    assert plan.trigger is None

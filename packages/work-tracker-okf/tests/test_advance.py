import difflib
from datetime import date

import pytest
from okf_io import Bundle, parse
from work_helpers import make_item
from work_tracker_okf.advance import AdvancePlan, FieldChange, advance, apply
from work_tracker_okf.items import load_items
from work_tracker_okf.workflow import PLAN_OR_EXECUTE, RouteResult, Transition

TODAY = date(2026, 8, 10)


def _plan_for(items, slug, **kwargs) -> AdvancePlan:
    return advance(items, slug, today=TODAY, **kwargs)


def _keys(plan: AdvancePlan) -> list[str]:
    return [change.key for change in plan.changes]


# --- refusals -------------------------------------------------------------


def test_an_unknown_slug_is_refused():
    plan = _plan_for([make_item("a")], "b")
    assert plan.refusal == "unknown-slug"
    assert plan.changes == ()


def test_a_blocked_route_is_refused_with_the_blockers_as_detail():
    plan = _plan_for([make_item("a", type="Widget")], "a")
    assert plan.refusal == "blocked"
    assert "Widget" in plan.detail


def test_a_satisfied_gate_with_nothing_to_advance_is_still_advanceable():
    """An epic whose children are all terminal has an on_complete: it is a
    satisfied gate, not a refusal."""
    items = [
        make_item("epic", type="Epic", phase="execute", workflow_status="accepted"),
        make_item("kid", parent="epic", workflow_status="resolved"),
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
    plan = _plan_for([make_item("feat", phase="execute", workflow_status="accepted")], "feat")
    assert plan.refusal == "owner-required"
    assert plan.changes == ()


def test_an_owner_already_on_the_page_satisfies_the_requirement():
    items = [make_item("feat", phase="execute", workflow_status="accepted", owner="human:pat")]
    assert _plan_for(items, "feat").refusal is None


def test_resolved_in_is_required_to_finish():
    plan = _plan_for([make_item("feat", phase="finish", workflow_status="in-progress")], "feat")
    assert plan.refusal == "resolved-in-required"


def test_open_children_refuse_a_feature_finishing():
    items = [
        make_item("feat", type="Feature", phase="execute", workflow_status="in-progress"),
        make_item("kid", parent="feat", workflow_status="open"),
    ]
    plan = _plan_for(items, "feat")
    assert plan.refusal == "children-open"
    assert "kid" in plan.detail
    assert plan.changes == ()


def test_every_refusal_carries_an_empty_change_list():
    """What makes `apply` inherently safe: there is nothing to write."""
    cases = [
        ([make_item("a")], "b"),
        ([make_item("a", type="Widget")], "a"),
        ([make_item("bug", type="Bug", phase="design")], "bug"),
        ([make_item("feat", phase="execute", workflow_status="accepted")], "feat"),
        ([make_item("feat", phase="finish", workflow_status="in-progress")], "feat"),
    ]
    for items, slug in cases:
        plan = _plan_for(items, slug)
        assert plan.refusal is not None, slug
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
    assert _keys(plan) == ["phase", "workflow_status", "status", "effort", "owner", "updated"]


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
    items = load_items(minimal_bundle)
    document = _document(minimal_bundle, "work/tech-debt-delta")
    before = document.serialize()
    plan = _plan_for(items, "tech-debt-delta")
    assert plan.refusal is None
    apply(document, plan)
    changed = {line[1:].split(":")[0].strip() for line in _diff(before, document.serialize())}
    assert changed == {key for key in _keys(plan)} | {"updated"} - {""}


def test_updated_is_written_bare_not_quoted(minimal_bundle: Bundle):
    """A `str` that would re-parse as a date comes back quoted; a `date` does
    not, and every authored page in this lane is bare."""
    items = load_items(minimal_bundle)
    document = _document(minimal_bundle, "work/tech-debt-delta")
    apply(document, _plan_for(items, "tech-debt-delta"))
    assert "updated: 2026-08-10" in document.serialize()
    assert "updated: '2026-08-10'" not in document.serialize()


def test_status_is_inserted_after_tags_while_phase_appends(minimal_bundle: Bundle):
    """`status` is in okf-io's PREFERRED_KEY_ORDER; `phase` is not."""
    items = load_items(minimal_bundle)
    document = _document(minimal_bundle, "work/test-gap-epsilon")
    # Strip both keys first so the insertion positions are observable.
    document.delete("status")
    document.delete("phase")
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
    """`broken-eta` projects with type='' and workflow_status='', which fails
    validation row 1. The refusal is data; the raise is unreachable."""
    items = load_items(minimal_bundle)
    plan = _plan_for(items, "broken-eta")
    assert plan.refusal == "blocked"
    assert plan.changes == ()


def test_apply_raises_nothing_of_its_own_on_an_unparseable_document():
    """`Document.set` raises on a document that failed to parse -- the only
    raise anywhere on this path. A refused plan never gets there."""
    document = parse("---\n: :\nbroken\n---\n\nbody\n")
    plan = _plan_for([make_item("a")], "b")
    assert plan.refusal == "unknown-slug"
    with pytest.raises(AssertionError):
        apply(document, plan)


def test_advance_stamps_worktree_and_branch_when_supplied() -> None:
    items = (make_item("x", type="Feature", workflow_status="open", phase="design"),)
    plan = _plan_for(items, "x", worktree="/tmp/wt/x", branch="feature/x")
    assert plan.refusal is None
    changed = {change.key: change.after for change in plan.changes}
    assert changed["worktree"] == "/tmp/wt/x"
    assert changed["branch"] == "feature/x"


def test_advance_without_provenance_keywords_changes_nothing_extra() -> None:
    items = (make_item("x", type="Feature", workflow_status="open", phase="design"),)
    assert "worktree" not in _keys(_plan_for(items, "x"))
    assert "branch" not in _keys(_plan_for(items, "x"))


def test_advance_does_not_rewrite_an_unchanged_worktree() -> None:
    items = (make_item("x", type="Feature", workflow_status="open", phase="design", worktree="/tmp/wt/x"),)
    assert "worktree" not in _keys(_plan_for(items, "x", worktree="/tmp/wt/x"))

from work_helpers import make_item
from work_tracker_okf.dependencies import DependencyEdge, DependencyFact
from work_tracker_okf.hierarchy import ChildRollup
from work_tracker_okf.workflow import state_for


def test_an_unknown_slug_has_no_state():
    assert state_for([make_item("a")], "b") is None


def test_a_childless_feature_carries_no_rollup():
    """No gate fires, and the JSON contract shows null."""
    state = state_for([make_item("feat", type="Feature")], "feat")
    assert state is not None
    assert state.child_rollup is None


def test_a_childless_epic_keeps_its_zero_rollup():
    """The epic's no-children blocker is phrased from it."""
    state = state_for([make_item("epic", type="Epic")], "epic")
    assert state is not None
    assert state.child_rollup == ChildRollup(0, 0, ())


def test_a_non_parent_type_never_carries_a_rollup():
    items = [make_item("bug", type="Bug"), make_item("kid", parent="bug", workflow_status="open")]
    state = state_for(items, "bug")
    assert state is not None
    assert state.child_rollup is None


def test_a_feature_with_children_carries_the_rollup():
    items = [
        make_item("feat", type="Feature"),
        make_item("kid", parent="feat", workflow_status="open"),
    ]
    state = state_for(items, "feat")
    assert state is not None
    assert state.child_rollup == ChildRollup(1, 0, ("kid",))


def test_an_archived_dependency_reads_as_met():
    """work-io needed a second loader for this and documented the bug it fixed:
    a resolved-and-archived dependency reading back as unmet."""
    items = [
        make_item("item", depends_on=(DependencyEdge("gone"),)),
        make_item("gone", archived=True, workflow_status="resolved"),
    ]
    state = state_for(items, "item")
    assert state is not None
    assert state.dependency_edges == (DependencyEdge("gone"),)
    assert state.dependency_facts == (DependencyFact("gone", known=True, terminal=True, status="resolved"),)


def test_a_terminal_parent_dependency_is_still_a_structural_issue():
    items = [
        make_item("parent", type="Epic", workflow_status="resolved"),
        make_item("child", parent="parent", phase="execute", depends_on=(DependencyEdge("parent"),)),
    ]
    state = state_for(items, "child")
    assert state is not None
    assert {issue.code for issue in state.dependency_issues} == {"targets-parent"}


def test_an_archived_child_counts_toward_terminal():
    items = [
        make_item("epic", type="Epic"),
        make_item("kid", parent="epic", archived=True, workflow_status="resolved"),
    ]
    state = state_for(items, "epic")
    assert state is not None
    assert state.child_rollup == ChildRollup(1, 1, ())


def test_the_effort_override_wins_over_the_page():
    items = [make_item("item", effort="small")]
    state = state_for(items, "item", effort="large")
    assert state is not None
    assert state.effort == "large"


def test_without_an_override_the_page_value_stands():
    state = state_for([make_item("item", effort="small")], "item")
    assert state is not None
    assert state.effort == "small"


def test_the_remaining_fields_come_straight_off_the_item():
    items = [
        make_item(
            "item",
            type="Bug",
            workflow_status="in-progress",
            phase="execute",
            has_plan_doc=True,
        )
    ]
    state = state_for(items, "item")
    assert state is not None
    assert (state.type, state.workflow_status, state.phase) == ("Bug", "in-progress", "execute")
    assert state.has_plan_doc is True

from work_helpers import make_item
from work_tracker_okf.dependencies import DependencyEdge, DependencyFact
from work_tracker_okf.hierarchy import ChildRollup
from work_tracker_okf.workflow import state_for


def test_an_unknown_path_has_no_state() -> None:
    assert state_for([make_item("a")], "work/b") is None


def test_a_childless_feature_carries_no_rollup() -> None:
    state = state_for([make_item("feat", type="Feature")], "work/feat")
    assert state is not None and state.child_rollup is None


def test_childless_release_and_epic_keep_zero_rollups() -> None:
    for type_name in ("Release", "Epic"):
        state = state_for([make_item("parent", type=type_name)], "work/parent")
        assert state is not None and state.child_rollup == ChildRollup(0, 0, ())


def test_a_feature_with_children_carries_full_paths() -> None:
    child_path = "work/feat/children/bug-child"
    items = [
        make_item("feat", type="Feature", active_child_paths=(child_path,)),
        make_item(
            child_path,
            type="Bug",
            parent_path="work/feat",
            ancestor_paths=("work/feat",),
            work_status="open",
        ),
    ]
    state = state_for(items, "work/feat")
    assert state is not None
    assert state.child_rollup == ChildRollup(1, 0, (child_path,))


def test_an_archived_dependency_reads_as_met() -> None:
    edge = DependencyEdge("work/_archive/bug-gone", "execute", "resolved")
    items = [
        make_item("item", dependency_edges=(edge,)),
        make_item("work/_archive/bug-gone", archived=True, work_status="resolved"),
    ]
    state = state_for(items, "work/item")
    assert state is not None
    assert state.dependency_edges == (edge,)
    assert state.dependency_facts == (
        DependencyFact("work/_archive/bug-gone", known=True, terminal=True, status="resolved"),
    )


def test_a_parent_dependency_is_a_structural_issue() -> None:
    parent_path = "work/epic-parent"
    child_path = f"{parent_path}/children/feature-child"
    edge = DependencyEdge(parent_path, "execute", "resolved")
    items = [
        make_item(parent_path, type="Epic", work_status="resolved"),
        make_item(
            child_path,
            parent_path=parent_path,
            ancestor_paths=(parent_path,),
            phase="execute",
            dependency_edges=(edge,),
        ),
    ]
    state = state_for(items, child_path)
    assert state is not None
    assert {issue.code for issue in state.dependency_issues} == {"targets-parent"}


def test_effort_override_and_projected_fields_are_preserved() -> None:
    item = make_item(
        "item",
        type="Bug",
        work_status="in-progress",
        phase="execute",
        effort="small",
        has_plan_artifact=True,
        has_design_artifact=True,
    )
    state = state_for((item,), item.path, effort="large")
    assert state is not None
    assert (state.type, state.work_status, state.phase, state.effort) == ("Bug", "in-progress", "execute", "large")
    assert state.has_plan_doc and state.has_spec_doc

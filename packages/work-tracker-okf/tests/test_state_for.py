from pathlib import Path

import pytest
from work_helpers import load_written_items, make_item, write_item
from work_tracker_okf.dependencies import DependencyEdge, DependencyFact
from work_tracker_okf.hierarchy import ChildRollup
from work_tracker_okf.workflow import route, state_for


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
        make_item("feat", type="Feature", child_paths=(child_path,)),
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


def test_open_descendants_sees_past_a_terminal_direct_child() -> None:
    child_path = "work/feat/children/done"
    grandchild_path = f"{child_path}/children/gc"
    items = [
        make_item("feat", type="Feature", child_paths=(child_path,)),
        make_item(
            child_path,
            type="Feature",
            parent_path="work/feat",
            ancestor_paths=("work/feat",),
            work_status="resolved",
            child_paths=(grandchild_path,),
        ),
        make_item(
            grandchild_path,
            type="Bug",
            parent_path=child_path,
            ancestor_paths=("work/feat", child_path),
            work_status="open",
        ),
    ]
    state = state_for(items, "work/feat")
    assert state is not None
    assert state.child_rollup == ChildRollup(1, 1, ())
    assert state.open_descendants == (grandchild_path,)


def test_a_feature_with_an_open_grandchild_still_requires_children_terminal() -> None:
    """Fixture 3 (design §3): a resolved direct child hiding an open grandchild
    must not let the feature finish as if its children were all terminal."""
    child_path = "work/feat/children/done"
    grandchild_path = f"{child_path}/children/gc"
    items = [
        make_item(
            "feat",
            type="Feature",
            phase="execute",
            work_status="in-progress",
            child_paths=(child_path,),
        ),
        make_item(
            child_path,
            type="Feature",
            parent_path="work/feat",
            ancestor_paths=("work/feat",),
            work_status="resolved",
            child_paths=(grandchild_path,),
        ),
        make_item(
            grandchild_path,
            type="Bug",
            parent_path=child_path,
            ancestor_paths=("work/feat", child_path),
            work_status="open",
        ),
    ]
    state = state_for(items, "work/feat")
    assert state is not None
    result = route(state)
    assert result.on_complete is not None
    assert result.on_complete.requires == ("children-terminal",)


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


_PARENT_AT_FINISH = "type: Epic\nwork_status: in-progress\nphase: finish\n"


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("branch: epic/integration-1a2b3c4d\nworktree: /wt/integration\n", True),
        ("branch: epic/integration-1a2b3c4d\n", True),
        ("worktree: /wt/integration\n", False),
        ("", False),
        ("branch:\n", False),
        ('branch: ""\n', False),
        ("branch: 42\n", False),
        ('branch: "   "\n', True),
    ],
    ids=[
        "branch-and-worktree",
        "branch-only",
        "worktree-only",
        "absent",
        "null",
        "empty",
        "non-string",
        "whitespace",
    ],
)
def test_state_for_carries_branch_ownership_from_frontmatter(tmp_path: Path, stamp: str, expected: bool) -> None:
    """Only `branch:` establishes ownership, by the existing tolerant projection:
    a worktree alone does not, and whitespace is still a (projected) stamp."""
    write_item(tmp_path, "parent", _PARENT_AT_FINISH + stamp)
    state = state_for(load_written_items(tmp_path), "work/parent")
    assert state is not None
    assert state.has_branch is expected


def test_state_for_carries_branch_ownership_for_every_type() -> None:
    for type_name in ("Epic", "Release", "Feature", "Bug"):
        stamped = state_for([make_item("item", type=type_name, branch="b/x-1")], "work/item")
        unstamped = state_for([make_item("item", type=type_name)], "work/item")
        assert stamped is not None and stamped.has_branch is True
        assert unstamped is not None and unstamped.has_branch is False

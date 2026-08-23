from work_helpers import make_item
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.graph import cycle_nodes
from work_tracker_okf.hierarchy import (
    ChildRollup,
    active_nonterminal_descendants,
    child_gated_node,
    child_rollup,
    descend,
    nearest_epic,
    nearest_parent,
    unknown_depends_on,
)


def test_child_rollup_reads_only_direct_path_children() -> None:
    parent = "work/release"
    child = "work/release/children/epic"
    grandchild = f"{child}/children/feature"
    items = (
        make_item(parent, type="Release", active_child_paths=(child,)),
        make_item(child, parent_path=parent, work_status="open"),
        make_item(grandchild, parent_path=child, work_status="resolved"),
    )
    assert child_rollup(items, parent) == ChildRollup(total=1, terminal=0, open_paths=(child,))


def test_descend_is_iterative_past_python_recursion_depth() -> None:
    paths = [f"work/n-{index}" for index in range(1081)]
    items = []
    for index, path in enumerate(paths):
        child_paths = (paths[index + 1],) if index + 1 < len(paths) else ()
        items.append(
            make_item(
                path,
                type="Epic" if child_paths else "Bug",
                phase="execute" if child_paths else None,
                parent_path=paths[index - 1] if index else None,
                active_child_paths=child_paths,
            )
        )
    result = descend(items, paths[0])
    assert result.leaf == paths[-1]
    assert len(result.path) == 1081


def test_nearest_epic_uses_derived_ancestors_without_a_depth_cap() -> None:
    paths = [f"work/n-{index}" for index in range(1081)]
    items = tuple(
        make_item(path, type="Epic" if index == 0 else "Bug", parent_path=paths[index - 1] if index else None)
        for index, path in enumerate(paths)
    )
    assert nearest_epic(items, paths[-1]) == "work/n-0"


def test_cycle_analysis_is_iterative_past_python_recursion_depth() -> None:
    graph = {f"n-{i}": [f"n-{i + 1}"] for i in range(1080)} | {"n-1080": []}
    assert cycle_nodes(graph) == ()


def test_cycle_analysis_returns_every_node_in_a_cycle() -> None:
    assert cycle_nodes({"a": ["b"], "b": ["c"], "c": ["a"], "outside": []}) == ("a", "b", "c")


def test_hierarchy_queries_handle_unknown_missing_archived_and_cycles() -> None:
    parent = make_item(
        "work/release",
        type="Release",
        active_child_paths=("work/missing", "work/feature"),
    )
    feature = make_item(
        "work/feature",
        parent_path=parent.path,
        active_child_paths=("work/bug", "work/release"),
    )
    bug = make_item("work/bug", type="Bug", parent_path=feature.path, archived=True)
    items = (parent, feature, bug)

    assert child_rollup(items, "work/unknown") == ChildRollup(0, 0, ())
    assert active_nonterminal_descendants(items, "work/unknown") == ()
    assert active_nonterminal_descendants(items, parent.path) == (feature.path,)
    assert nearest_parent(items, bug.path) == feature.path
    assert nearest_parent(items, "work/unknown") is None
    assert nearest_epic(items, bug.path) is None
    assert nearest_epic(items, "work/unknown") is None


def test_dispatch_helpers_cover_gating_unknown_edges_and_blocked_descent() -> None:
    release = make_item(
        "work/release",
        type="Release",
        phase="execute",
        active_child_paths=("work/feature",),
    )
    edge = DependencyEdge("work/missing", "execute", "resolved")
    feature = make_item(
        "work/feature",
        work_status="open",
        phase="execute",
        parent_path=release.path,
        dependency_edges=(edge,),
    )
    items = (release, feature)

    assert unknown_depends_on(items, (edge,)) == {"work/missing": None}
    assert child_gated_node(release, (feature,))
    assert not child_gated_node(feature, (make_item("work/done", work_status="resolved"),))
    assert descend(items, "work/unknown").blocked_at == "work/unknown"
    result = descend(items, release.path)
    assert result.leaf is None
    assert result.blocked_at == release.path

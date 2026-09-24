from work_helpers import make_item
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.graph import cycle_nodes
from work_tracker_okf.hierarchy import (
    ChildRollup,
    active_nonterminal_descendants,
    child_gated,
    child_rollup,
    decision_owner,
    declared_repo,
    descend,
    nearest_epic,
    nearest_parent,
    sweep_eligible,
    unknown_depends_on,
)


def test_child_rollup_reads_only_direct_path_children() -> None:
    parent = "work/release"
    child = "work/release/children/epic"
    grandchild = f"{child}/children/feature"
    items = (
        make_item(parent, type="Release", child_paths=(child,)),
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
                child_paths=child_paths,
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
        child_paths=("work/missing", "work/feature"),
    )
    feature = make_item(
        "work/feature",
        parent_path=parent.path,
        child_paths=("work/bug", "work/release"),
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


def test_decision_owner_prefers_the_nearest_parent_and_falls_back_to_the_item() -> None:
    epic = make_item(
        "work/epic-a",
        type="Epic",
        child_paths=("work/epic-a/children/bug-a",),
    )
    bug = make_item(
        "work/epic-a/children/bug-a",
        type="Bug",
        parent_path=epic.path,
        ancestor_paths=(epic.path,),
    )
    lone = make_item("work/bug-lone", type="Bug")
    feature = make_item("work/feature-f", type="Feature")
    items = (epic, bug, lone, feature)

    assert decision_owner(items, bug.path) == epic.path
    assert decision_owner(items, feature.path) == feature.path
    assert decision_owner(items, lone.path) == lone.path
    assert decision_owner(items, "work/unknown") is None


def test_dispatch_helpers_cover_gating_unknown_edges_and_blocked_descent() -> None:
    release = make_item(
        "work/release",
        type="Release",
        phase="execute",
        child_paths=("work/feature",),
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
    assert child_gated(items, release)
    assert not child_gated(items, feature)
    assert descend(items, "work/unknown").blocked_at == "work/unknown"
    result = descend(items, release.path)
    assert result.leaf is None
    assert result.blocked_at == release.path


def test_child_gated_sees_open_grandchildren_beneath_a_terminal_child() -> None:
    epic = make_item(
        "work/epic",
        type="Epic",
        phase="execute",
        child_paths=("work/epic/children/feat",),
    )
    feat = make_item(
        "work/epic/children/feat",
        type="Feature",
        work_status="resolved",
        parent_path=epic.path,
        child_paths=("work/epic/children/feat/children/gc",),
    )
    grandchild = make_item(
        "work/epic/children/feat/children/gc",
        type="Bug",
        work_status="open",
        parent_path=feat.path,
    )
    items = (epic, feat, grandchild)

    assert child_gated(items, epic)


def test_child_gated_admits_the_finish_phase_window_for_epics() -> None:
    epic = make_item(
        "work/epic",
        type="Epic",
        phase="finish",
        child_paths=("work/epic/children/late",),
    )
    late = make_item(
        "work/epic/children/late",
        type="Bug",
        work_status="open",
        parent_path=epic.path,
    )
    items = (epic, late)

    assert child_gated(items, epic)


def test_sweep_eligible_is_true_only_for_a_terminal_top_level_item_with_a_terminal_subtree() -> None:
    root = make_item(
        "work/epic-done",
        type="Epic",
        work_status="resolved",
        child_paths=("work/epic-done/children/bug-a",),
    )
    child = make_item(
        "work/epic-done/children/bug-a",
        type="Bug",
        work_status="wontfix",
        parent_path=root.path,
        ancestor_paths=(root.path,),
    )
    assert sweep_eligible((root, child), root) is True
    assert sweep_eligible((root, child), child) is False


def test_sweep_eligible_is_false_for_a_root_with_an_open_descendant_at_any_depth() -> None:
    root = make_item(
        "work/epic-held",
        type="Epic",
        work_status="resolved",
        child_paths=("work/epic-held/children/feature-a",),
    )
    mid = make_item(
        "work/epic-held/children/feature-a",
        work_status="resolved",
        parent_path=root.path,
        ancestor_paths=(root.path,),
        child_paths=("work/epic-held/children/feature-a/children/bug-b",),
    )
    leaf = make_item(
        "work/epic-held/children/feature-a/children/bug-b",
        type="Bug",
        work_status="open",
        parent_path=mid.path,
        ancestor_paths=(root.path, mid.path),
    )
    assert sweep_eligible((root, mid, leaf), root) is False


def test_sweep_eligible_is_false_for_open_and_archived_roots() -> None:
    assert sweep_eligible((), make_item("work/bug-open", work_status="open")) is False
    archived = make_item("work/_archive/bug-old", work_status="resolved", archived=True)
    assert sweep_eligible((archived,), archived) is False


_E = "work/epic-a"
_F = f"{_E}/children/feature-b"
_B = f"{_F}/children/bug-c"


def _chain(epic: str | None = None, feature: str | None = None, bug: str | None = None):
    items = (
        make_item(_E, type="Epic", repo=epic),
        make_item(_F, type="Feature", repo=feature, parent_path=_E, ancestor_paths=(_E,)),
        make_item(_B, type="Bug", repo=bug, parent_path=_F, ancestor_paths=(_E, _F)),
    )
    return {item.path: item for item in items}


def test_declared_repo_reads_the_item_s_own_repo() -> None:
    index = _chain(bug="ui")
    assert declared_repo(index[_B], index) == ("ui", _B)


def test_declared_repo_inherits_from_an_ancestor() -> None:
    index = _chain(epic="code")
    assert declared_repo(index[_B], index) == ("code", _E)


def test_declared_repo_nearest_wins_across_three_levels() -> None:
    index = _chain(epic="code", feature="ui")
    assert declared_repo(index[_B], index) == ("ui", _F)
    assert declared_repo(index[_E], index) == ("code", _E)


def test_declared_repo_own_value_overrides_ancestors() -> None:
    index = _chain(epic="code", feature="ui", bug="docs")
    assert declared_repo(index[_B], index) == ("docs", _B)


def test_declared_repo_absent_everywhere() -> None:
    index = _chain()
    assert declared_repo(index[_B], index) == (None, None)


def test_declared_repo_skips_a_missing_ancestor() -> None:
    index = _chain(epic="code")
    del index[_F]
    assert declared_repo(index[_B], index) == ("code", _E)

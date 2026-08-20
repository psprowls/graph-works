from work_helpers import make_item
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.hierarchy import (
    WALK_DEPTH_CAP,
    ChildRollup,
    child_gated_node,
    child_rollup,
    descend,
    nearest_epic,
    unknown_depends_on,
)


def _family():
    return [
        make_item("epic-a", type="Epic", phase="execute", workflow_status="accepted"),
        make_item("kid-open", parent="epic-a", workflow_status="open", opened="2026-01-02"),
        make_item("kid-done", parent="epic-a", workflow_status="resolved", opened="2026-01-01"),
        make_item("kid-gone", parent="epic-a", workflow_status="superseded", archived=True),
        make_item("stranger"),
    ]


def test_child_rollup_counts_terminal_and_names_the_open():
    roll = child_rollup(_family(), "epic-a")
    assert roll == ChildRollup(total=3, terminal=2, open_slugs=("kid-open",))


def test_child_rollup_of_a_childless_parent_is_a_zero():
    assert child_rollup(_family(), "stranger") == ChildRollup(total=0, terminal=0, open_slugs=())


def test_an_archived_child_still_counts_toward_terminal():
    """The parent/child relationship is permanent; archiving is not detaching."""
    assert child_rollup(_family(), "epic-a").terminal == 2


def test_unknown_depends_on_hints_at_a_single_date_prefixed_match():
    items = [make_item("2026-01-01-fix-the-thing"), make_item("other")]
    edges = (DependencyEdge("fix-the-thing"),)
    assert unknown_depends_on(items, edges) == {"fix-the-thing": "2026-01-01-fix-the-thing"}


def test_unknown_depends_on_gives_no_hint_when_two_slugs_match():
    items = [make_item("2026-01-01-dupe"), make_item("2026-02-01-dupe")]
    assert unknown_depends_on(items, (DependencyEdge("dupe"),)) == {"dupe": None}


def test_unknown_depends_on_skips_values_that_name_a_real_item():
    assert unknown_depends_on(_family(), (DependencyEdge("kid-open"),)) == {}


def test_an_epic_at_plan_is_its_own_leaf_not_a_gated_node():
    """The children gate engages at execute; an epic at plan dispatches to
    decomposition, which is work of its own."""
    epic = make_item("epic-a", type="Epic", phase="plan")
    kids = [make_item("kid", parent="epic-a", workflow_status="open")]
    assert child_gated_node(epic, kids) is False


def test_an_epic_at_execute_with_open_children_is_gated():
    epic = make_item("epic-a", type="Epic", phase="execute")
    kids = [make_item("kid", parent="epic-a", workflow_status="open")]
    assert child_gated_node(epic, kids) is True


def test_a_feature_is_gated_at_both_execute_and_finish():
    kids = [make_item("kid", parent="feat", workflow_status="open")]
    for phase in ("execute", "finish"):
        assert child_gated_node(make_item("feat", type="Feature", phase=phase), kids) is True
    assert child_gated_node(make_item("feat", type="Feature", phase="plan"), kids) is False


def test_a_node_whose_children_are_all_terminal_is_never_gated():
    epic = make_item("epic-a", type="Epic", phase="execute")
    kids = [make_item("kid", parent="epic-a", workflow_status="resolved")]
    assert child_gated_node(epic, kids) is False


def test_descend_walks_to_the_actionable_leaf():
    items = [
        make_item("epic-a", type="Epic", phase="execute"),
        make_item("feat-b", type="Feature", phase="execute", parent="epic-a", workflow_status="accepted"),
        make_item("leaf-c", type="Bug", parent="feat-b", workflow_status="open"),
    ]
    result = descend(items, "epic-a")
    assert result.leaf == "leaf-c"
    assert result.path == ("epic-a", "feat-b", "leaf-c")


def test_descend_prefers_in_progress_then_accepted_then_oldest_open():
    items = [
        make_item("epic-a", type="Epic", phase="execute"),
        make_item("c-open", parent="epic-a", workflow_status="open", opened="2026-01-01"),
        make_item("b-accepted", parent="epic-a", workflow_status="accepted", opened="2026-03-01"),
        make_item("a-live", parent="epic-a", workflow_status="in-progress", opened="2026-05-01"),
    ]
    assert descend(items, "epic-a").leaf == "a-live"


def test_descend_prefers_an_active_childs_parent_over_its_archived_twin() -> None:
    items = [
        make_item("epic", type="Epic", phase="execute"),
        make_item("other", type="Epic", phase="execute"),
        make_item("child", parent="other", workflow_status="open"),
        make_item(
            "child",
            parent="epic",
            workflow_status="open",
            archived=True,
            path="work/_archive/child.md",
        ),
    ]

    assert descend(items, "epic").leaf == "epic"


def test_a_mitigated_child_holds_the_gate_but_is_never_the_target():
    items = [
        make_item("epic-a", type="Epic", phase="execute"),
        make_item("held", parent="epic-a", workflow_status="mitigated"),
    ]
    result = descend(items, "epic-a")
    assert result.leaf is None
    assert result.blocked_at == "epic-a"
    assert "no dep-ready child" in (result.reason or "")


def test_a_child_blocked_on_an_unfinished_dependency_is_not_a_candidate():
    items = [
        make_item("epic-a", type="Epic", phase="execute"),
        make_item(
            "kid",
            parent="epic-a",
            phase="execute",
            workflow_status="open",
            depends_on=(DependencyEdge("blocker"),),
        ),
        make_item("blocker", workflow_status="open"),
    ]
    assert descend(items, "epic-a").leaf is None


def test_descend_skips_a_child_blocked_at_its_next_phase() -> None:
    items = (
        make_item("epic", type="Epic", phase="execute"),
        make_item("blocked", parent="epic", phase=None, depends_on=(DependencyEdge("dep", blocks="design"),)),
        make_item("ready", parent="epic", phase=None, opened="2026-08-02"),
        make_item("dep", phase="execute", workflow_status="in-progress"),
    )
    assert descend(items, "epic").leaf == "ready"


def test_descend_allows_repeated_slug_when_only_one_gate_is_satisfied() -> None:
    child = make_item(
        "child",
        parent="epic",
        phase="plan",
        depends_on=(
            DependencyEdge("dep", blocks="plan", needs="design"),
            DependencyEdge("dep", blocks="plan", needs="execute"),
        ),
    )
    result = descend(
        (make_item("epic", type="Epic", phase="execute"), child, make_item("dep", phase="plan")),
        "epic",
    )
    assert result.leaf is None
    assert result.blocked_at == "epic"


def test_descend_of_an_unknown_slug_reports_it():
    result = descend([], "nobody")
    assert result.leaf is None
    assert result.blocked_at == "nobody"
    assert "unknown slug" in (result.reason or "")


def test_descend_detects_a_parent_cycle():
    items = [
        make_item("a", type="Epic", phase="execute", parent="b", workflow_status="open"),
        make_item("b", type="Epic", phase="execute", parent="a", workflow_status="open"),
    ]
    result = descend(items, "a")
    assert result.leaf is None
    assert "parent cycle detected" in (result.reason or "")


def test_descend_stops_at_the_depth_cap():
    """A chain longer than the cap terminates even without a repeated slug."""
    depth = WALK_DEPTH_CAP + 2
    items = [make_item("n0", type="Epic", phase="execute", workflow_status="open")]
    for i in range(1, depth):
        items.append(make_item(f"n{i}", type="Epic", phase="execute", parent=f"n{i - 1}", workflow_status="open"))
    result = descend(items, "n0")
    assert result.leaf is None
    assert f"depth cap ({WALK_DEPTH_CAP})" in (result.reason or "")
    assert len(result.path) == WALK_DEPTH_CAP


def _lineage():
    return [
        make_item("epic-top", type="Epic"),
        make_item("feature-mid", type="Feature", parent="epic-top"),
        make_item("bug-leaf", type="Bug", parent="feature-mid"),
        make_item("orphan", type="Bug"),
        make_item("lost", type="Bug", parent="no-such-item"),
    ]


def test_nearest_epic_walks_past_a_feature_parent():
    assert nearest_epic(_lineage(), "bug-leaf") == "epic-top"


def test_nearest_epic_prefers_an_active_items_parent_over_its_archived_twin():
    items = [
        make_item("current", type="Epic"),
        make_item("historical", type="Epic", archived=True),
        make_item("child", parent="current"),
        make_item("child", parent="historical", archived=True, path="work/_archive/child.md"),
    ]

    assert nearest_epic(items, "child") == "current"


def test_an_epic_is_its_own_nearest_epic():
    assert nearest_epic(_lineage(), "epic-top") == "epic-top"


def test_nearest_epic_of_a_parentless_non_epic_is_none():
    assert nearest_epic(_lineage(), "orphan") is None


def test_nearest_epic_of_an_unknown_parent_is_none():
    assert nearest_epic(_lineage(), "lost") is None


def test_nearest_epic_of_an_unknown_slug_is_none():
    assert nearest_epic(_lineage(), "nobody") is None


def test_nearest_epic_terminates_on_a_parent_cycle():
    """A `parent` chain that closes on itself is `graph.parent-cycle`'s finding,
    not this walk's problem — it must return, not loop."""
    cycle = [
        make_item("a", type="Bug", parent="b"),
        make_item("b", type="Bug", parent="a"),
    ]
    assert nearest_epic(cycle, "a") is None


def test_nearest_epic_is_bounded_by_the_shared_depth_cap():
    chain = [make_item(f"n{i}", type="Bug", parent=f"n{i + 1}") for i in range(WALK_DEPTH_CAP + 5)]
    chain.append(make_item(f"n{WALK_DEPTH_CAP + 5}", type="Epic"))
    assert nearest_epic(chain, "n0") is None

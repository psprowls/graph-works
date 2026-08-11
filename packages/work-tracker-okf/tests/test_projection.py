import pytest
from work_helpers import make_item
from work_tracker_okf.hierarchy import ChildRollup
from work_tracker_okf.projection import (
    MAX_ALTERNATIVES,
    NON_ACTIONABLE_STATUSES,
    ResumeItem,
    resolve,
    rollup,
    select_resume,
)


def test_the_counts_cover_active_items_only():
    """An archive that grows without bound would otherwise swamp the numbers a
    human reads."""
    items = [
        make_item("a", type="Feature", workflow_status="open", phase="design"),
        make_item("b", type="Bug", workflow_status="resolved", phase="done", archived=True),
    ]
    result = rollup(items)
    assert result.total == 1
    assert dict(result.by_type) == {"Feature": 1}
    assert dict(result.by_workflow_status) == {"open": 1}
    assert dict(result.by_phase) == {"design": 1}


def test_an_item_with_no_phase_is_counted_nowhere_in_by_phase():
    assert dict(rollup([make_item("a", phase=None)]).by_phase) == {}


def test_the_child_rollups_span_the_archive():
    items = [
        make_item("epic", type="Epic"),
        make_item("kid", parent="epic", archived=True, workflow_status="resolved"),
    ]
    assert dict(rollup(items).children) == {"epic": ChildRollup(1, 1, ())}


def test_a_childless_epic_keeps_its_zero_and_a_childless_feature_gets_no_entry():
    items = [make_item("epic", type="Epic"), make_item("feat", type="Feature")]
    assert dict(rollup(items).children) == {"epic": ChildRollup(0, 0, ())}


def test_the_mappings_are_read_only():
    """A frozen dataclass holding a mutable dict is frozen in name only."""
    result = rollup([make_item("a")])
    with pytest.raises(TypeError):
        result.by_type["Widget"] = 1  # type: ignore[index]


def test_resume_orders_by_updated_descending():
    items = [
        make_item("old", updated="2026-01-01", title="Old"),
        make_item("new", updated="2026-03-01", title="New"),
    ]
    selection = select_resume(items)
    assert selection is not None
    assert selection.primary == ResumeItem(slug="new", title="New")


def test_a_same_updated_tie_breaks_on_slug_ascending():
    """A real behaviour change from work-io's mtime ordering: deterministic,
    which mtime was not."""
    items = [
        make_item("b-item", updated="2026-03-01"),
        make_item("a-item", updated="2026-03-01"),
    ]
    selection = select_resume(items)
    assert selection is not None
    assert selection.primary.slug == "a-item"


def test_terminal_and_mitigated_items_are_not_actionable():
    assert {"resolved", "wontfix", "superseded", "mitigated"} == NON_ACTIONABLE_STATUSES
    items = [make_item(status, workflow_status=status) for status in sorted(NON_ACTIONABLE_STATUSES)]
    assert select_resume(items) is None


def test_archived_items_are_never_resumed():
    assert select_resume([make_item("a", archived=True)]) is None


def test_nothing_actionable_is_none_not_an_empty_selection():
    assert select_resume([]) is None


def test_the_alternatives_are_capped():
    items = [make_item(f"item-{i}", updated=f"2026-01-{i + 1:02d}") for i in range(MAX_ALTERNATIVES + 3)]
    selection = select_resume(items)
    assert selection is not None
    assert len(selection.alternatives) == MAX_ALTERNATIVES


def test_resolve_finds_the_active_page(minimal_root):
    found = resolve(minimal_root, "feature-beta")
    assert found is not None
    assert found.name == "feature-beta.md"
    assert found.parent.name == "work"


def test_resolve_finds_the_archived_page(minimal_root):
    found = resolve(minimal_root, "bug-theta")
    assert found is not None
    assert found.parent.name == "_archive"


def test_resolve_prefers_the_active_candidate(tmp_path):
    """An active item is the one a caller means."""
    (tmp_path / "work" / "_archive").mkdir(parents=True)
    (tmp_path / "work" / "dupe.md").write_text("active", encoding="utf-8")
    (tmp_path / "work" / "_archive" / "dupe.md").write_text("archived", encoding="utf-8")
    found = resolve(tmp_path, "dupe")
    assert found is not None
    assert found.read_text(encoding="utf-8") == "active"


def test_resolve_returns_none_for_neither(minimal_root):
    assert resolve(minimal_root, "no-such-item") is None

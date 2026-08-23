from work_helpers import make_item
from work_tracker_okf.hierarchy import ChildRollup
from work_tracker_okf.projection import ResumeItem, rollup, select_resume


def test_rollup_keys_children_by_full_path() -> None:
    parent = "work/release"
    child = "work/release/children/epic"
    items = (
        make_item(parent, type="Release", active_child_paths=(child,)),
        make_item(child, parent_path=parent, work_status="resolved"),
    )
    assert dict(rollup(items).children) == {parent: ChildRollup(1, 1, ())}


def test_rollup_keeps_a_childless_release_parent_gate() -> None:
    release = make_item("work/release", type="Release")
    assert dict(rollup((release,)).children) == {release.path: ChildRollup(0, 0, ())}


def test_resume_selection_returns_a_path_keyed_item() -> None:
    selection = select_resume((make_item("work/a", updated="2026-01-01", title="A"),))
    assert selection is not None
    assert selection.primary == ResumeItem(path="work/a", title="A")

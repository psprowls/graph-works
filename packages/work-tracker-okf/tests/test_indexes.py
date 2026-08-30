from pathlib import Path

from okf_io import load_bundle
from work_helpers import make_item
from work_tracker_okf.indexes import (
    GENERATED_END,
    GENERATED_START,
    _required_lanes,
    plan_indexes,
    reconcile_marked_index,
    render_entry,
)
from work_tracker_okf.items import IGNORE, load_items


def test_lane_index_lists_direct_items_only_and_preserves_human_prose(path_native_root: Path) -> None:
    lane = "work/release-cutover/children"
    index = path_native_root / lane / "index.md"
    index.write_text("# Children\n\nHuman note.\n", encoding="utf-8")
    bundle = load_bundle(path_native_root, ignore=IGNORE)
    plan = plan_indexes(path_native_root, load_items(bundle), lanes=(lane,))[0]
    assert "Human note." in plan.after
    assert GENERATED_START in plan.after
    assert "epic-migration.md" in plan.after
    assert "feature-import.md" not in plan.after


def test_existing_generated_region_is_replaced_without_touching_surrounding_prose(path_native_root: Path) -> None:
    lane = "work/release-cutover/children"
    index = path_native_root / lane / "index.md"
    index.write_text(
        f"# Children\n\nBefore.\n\n{GENERATED_START}\n- stale\n{GENERATED_END}\n\nAfter.\n",
        encoding="utf-8",
    )

    plan = plan_indexes(path_native_root, load_items(load_bundle(path_native_root, ignore=IGNORE)), lanes=(lane,))[0]

    assert plan.before is not None
    assert plan.after.startswith("# Children\n\nBefore.\n\n")
    assert plan.after.endswith("\n\nAfter.\n")
    assert "- stale" not in plan.after
    assert plan.after.count(GENERATED_START) == 1
    assert plan.after.count(GENERATED_END) == 1


def test_shared_reconciler_accepts_pre_repaired_prose_and_generated_entries() -> None:
    before = f"# Children\n\nSee /new/path.md.\n\n{GENERATED_START}\n- stale\n{GENERATED_END}\n"

    after = reconcile_marked_index(before, ("- current",))

    assert "See /new/path.md." in after
    assert "- current" in after
    assert "- stale" not in after


def test_index_entries_are_sorted_by_filename() -> None:
    lane = "work/release/children"
    items = (
        make_item(f"{lane}/feature-zulu", title="Zulu", phase="execute"),
        make_item(f"{lane}/bug-alpha", type="Bug", title="Alpha", phase="plan"),
    )

    plan = plan_indexes(Path("/vault"), items, lanes=(lane,))[0]

    assert plan.entries == (
        "- [Bug: Alpha](bug-alpha.md) — open · plan",
        "- [Feature: Zulu](feature-zulu.md) — open · execute",
    )


def test_planner_covers_root_and_active_parent_child_lanes_without_archives_for_parents_with_no_archived_children(
    tmp_path: Path,
) -> None:
    release = "work/release-cutover"
    epic = f"{release}/children/epic-migration"
    feature = f"{epic}/children/feature-import"
    items = (
        make_item(release, type="Release"),
        make_item(epic, type="Epic", parent_path=release),
        make_item(feature, type="Feature", parent_path=epic),
    )

    lanes = {plan.lane for plan in plan_indexes(tmp_path, items)}

    assert lanes == {
        "work",
        "work/_archive",
        f"{release}/children",
        f"{epic}/children",
        f"{feature}/children",
    }


def test_render_entry_uses_the_direct_basename_and_visible_state() -> None:
    item = make_item(
        "work/release/children/feature-import",
        type="Feature",
        title="Import data",
        work_status="in-progress",
        phase="execute",
    )
    assert render_entry(item) == "- [Feature: Import data](feature-import.md) — in-progress · execute"


def test_an_active_parent_with_no_archived_children_requires_no_archive_lane() -> None:
    epic = make_item("work/epic-new", type="Epic", work_status="open")

    lanes = _required_lanes((epic,))

    assert "work/epic-new/children" in lanes
    assert "work/epic-new/children/_archive" not in lanes


def test_an_active_parent_that_has_archived_children_still_requires_its_archive_lane() -> None:
    epic = make_item(
        "work/epic-legacy",
        type="Epic",
        work_status="open",
        archived_child_paths=("work/epic-legacy/children/_archive/bug-old",),
    )

    lanes = _required_lanes((epic,))

    assert "work/epic-legacy/children/_archive" in lanes


def test_the_two_root_lanes_are_always_required() -> None:
    assert set(_required_lanes(())) == {"work", "work/_archive"}

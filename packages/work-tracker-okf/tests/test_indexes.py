from pathlib import Path

from okf_io import load_bundle
from work_helpers import make_item
from work_tracker_okf.indexes import (
    _required_lanes,
    is_direct_entry_target,
    parse_entry,
    plan_indexes,
    reconcile_entries,
    render_entry,
    strip_legacy_markers,
)
from work_tracker_okf.items import IGNORE, load_items


def test_is_direct_entry_target_accepts_a_bare_item_page() -> None:
    assert is_direct_entry_target("feature-import-legacy.md") is True


def test_is_direct_entry_target_rejects_index_md() -> None:
    assert is_direct_entry_target("index.md") is False


def test_is_direct_entry_target_rejects_a_path_with_a_separator() -> None:
    assert is_direct_entry_target("children/feature-import-legacy.md") is False
    assert is_direct_entry_target("../sibling-lane/feature-import-legacy.md") is False


def test_is_direct_entry_target_rejects_a_non_markdown_target() -> None:
    assert is_direct_entry_target("diagram.png") is False


def test_reconcile_entries_from_nothing_creates_an_items_section() -> None:
    entries = ("- [Epic: X](epic-x.md) — open · design",)
    result = reconcile_entries(None, entries)
    assert result == "# Items\n\n- [Epic: X](epic-x.md) — open · design\n"


def test_reconcile_entries_from_nothing_with_no_entries_is_empty() -> None:
    assert reconcile_entries(None, ()) == ""


def test_reconcile_entries_appends_a_new_section_after_existing_prose() -> None:
    before = "Some hand-written notes about this lane.\n"
    entries = ("- [Epic: X](epic-x.md) — open · design",)
    result = reconcile_entries(before, entries)
    assert result == ("Some hand-written notes about this lane.\n\n# Items\n\n- [Epic: X](epic-x.md) — open · design\n")


def test_reconcile_entries_refreshes_a_changed_entry_in_place() -> None:
    before = "# Items\n\n- [Epic: X](epic-x.md) — open · design\n"
    entries = ("- [Epic: X](epic-x.md) — in-progress · execute",)
    result = reconcile_entries(before, entries)
    assert result == "# Items\n\n- [Epic: X](epic-x.md) — in-progress · execute\n"


def test_reconcile_entries_prunes_a_stale_entry() -> None:
    before = "# Items\n\n- [Epic: X](epic-x.md) — open · design\n- [Epic: Y](epic-y.md) — open · design\n"
    entries = ("- [Epic: X](epic-x.md) — open · design",)  # Y left the lane
    result = reconcile_entries(before, entries)
    assert "epic-y.md" not in result
    assert result == "# Items\n\n- [Epic: X](epic-x.md) — open · design\n"


def test_reconcile_entries_inserts_a_missing_entry_in_filename_order() -> None:
    before = "# Items\n\n- [Epic: X](epic-x.md) — open · design\n"
    entries = (
        "- [Epic: A](epic-a.md) — open · design",
        "- [Epic: X](epic-x.md) — open · design",
    )
    result = reconcile_entries(before, entries)
    assert result == ("# Items\n\n- [Epic: A](epic-a.md) — open · design\n- [Epic: X](epic-x.md) — open · design\n")


def test_reconcile_entries_orders_prefix_colliding_slugs_the_same_regardless_of_history() -> None:
    """`feature-x` sorts before `feature-x-y` by basename, as `_direct_items`
    orders them -- but `feature-x-y.md` sorts before `feature-x.md` as a raw
    string (`-` < `.`). Every path to the final index must agree on the
    basename order, whichever entry was already present."""
    short = "- [Feature: X](feature-x.md) — open · design"
    long = "- [Feature: X Y](feature-x-y.md) — open · design"
    entries = (short, long)
    expected = f"# Items\n\n{short}\n{long}\n"

    from_nothing = reconcile_entries(None, entries)
    from_prose = reconcile_entries("# Items\n", entries)
    long_first = reconcile_entries(reconcile_entries(None, (long,)), entries)
    short_first = reconcile_entries(reconcile_entries(None, (short,)), entries)

    assert from_nothing == expected
    assert from_prose == expected
    assert long_first == expected
    assert short_first == expected


def test_reconcile_entries_never_touches_an_unrelated_link_bullet() -> None:
    before = (
        "# Items\n\n- [Epic: X](epic-x.md) — open · design\n\n# Subdirectories\n\n- [children/](children/index.md)\n"
    )
    entries = ("- [Epic: X](epic-x.md) — open · design",)
    result = reconcile_entries(before, entries)
    assert "- [children/](children/index.md)" in result


def test_reconcile_entries_never_touches_a_non_direct_item_link() -> None:
    before = (
        "# Items\n\n"
        "- [Epic: X](epic-x.md) — open · design\n"
        "- [Feature: Sibling](../sibling-lane/feature-sibling.md) — open · design\n"
    )
    entries = ("- [Epic: X](epic-x.md) — open · design",)
    result = reconcile_entries(before, entries)
    assert "../sibling-lane/feature-sibling.md" in result


def test_reconcile_entries_strips_legacy_marker_lines_and_keeps_entries() -> None:
    before = (
        "<!-- graph-works:work-items:start -->\n"
        "- [Epic: X](epic-x.md) — open · design\n"
        "<!-- graph-works:work-items:end -->\n"
    )
    entries = ("- [Epic: X](epic-x.md) — open · design",)
    result = reconcile_entries(before, entries)
    assert "graph-works:work-items" not in result
    assert result == "- [Epic: X](epic-x.md) — open · design\n"


def test_strip_legacy_markers_removes_only_the_two_marker_lines() -> None:
    before = (
        "# Lane\n\n"
        "<!-- graph-works:work-items:start -->\n"
        "- [Epic: Gone](epic-gone.md) — resolved · done\n"
        "<!-- graph-works:work-items:end -->\n\n"
        "Trailing prose.\n"
    )
    assert strip_legacy_markers(before) == (
        "# Lane\n\n- [Epic: Gone](epic-gone.md) — resolved · done\n\nTrailing prose.\n"
    )
    assert strip_legacy_markers("no markers\n") == "no markers\n"


def test_reconcile_entries_migrates_legacy_prose_and_entries_together() -> None:
    before = (
        "Some notes.\n\n"
        "<!-- graph-works:work-items:start -->\n"
        "- [Epic: X](epic-x.md) — open · design\n"
        "<!-- graph-works:work-items:end -->\n"
    )
    entries = ("- [Epic: X](epic-x.md) — open · design",)
    result = reconcile_entries(before, entries)
    assert result == "Some notes.\n\n- [Epic: X](epic-x.md) — open · design\n"


def test_reconcile_entries_reuses_a_dangling_items_heading_after_pruning_to_empty() -> None:
    before = "# Items\n\n- [Epic: X](epic-x.md) — open · design\n"
    emptied = reconcile_entries(before, ())
    result = reconcile_entries(emptied, ("- [Epic: Y](epic-y.md) — open · design",))
    assert result.count("# Items") == 1
    assert result == "# Items\n\n- [Epic: Y](epic-y.md) — open · design\n"


def test_reconcile_entries_reuses_dangling_heading_even_with_a_trailing_subdirectories_section() -> None:
    before = (
        "# Items\n\n- [Epic: X](epic-x.md) — open · design\n\n# Subdirectories\n\n- [children/](children/index.md)\n"
    )
    emptied = reconcile_entries(before, ())
    result = reconcile_entries(emptied, ("- [Epic: Y](epic-y.md) — open · design",))
    assert result.count("# Items") == 1
    assert "- [children/](children/index.md)" in result
    assert result.index("# Items") < result.index("# Subdirectories")


def test_lane_index_lists_direct_items_only_and_preserves_human_prose(path_native_root: Path) -> None:
    lane = "work/release-cutover/children"
    index = path_native_root / lane / "index.md"
    index.write_text("# Children\n\nHuman note.\n", encoding="utf-8")
    bundle = load_bundle(path_native_root, ignore=IGNORE)
    plan = plan_indexes(path_native_root, load_items(bundle), lanes=(lane,))[0]
    assert "# Children\n\nHuman note." in plan.after
    assert "# Items" in plan.after
    assert "epic-migration.md" in plan.after
    assert "feature-import.md" not in plan.after


def test_existing_items_section_is_refreshed_in_place_without_touching_surrounding_prose(
    path_native_root: Path,
) -> None:
    lane = "work/release-cutover/children"
    index = path_native_root / lane / "index.md"
    index.write_text(
        "# Children\n\nBefore.\n\n# Items\n\n- [stale](stale.md) — open · design\n",
        encoding="utf-8",
    )

    plan = plan_indexes(path_native_root, load_items(load_bundle(path_native_root, ignore=IGNORE)), lanes=(lane,))[0]

    assert plan.before is not None
    assert plan.after.startswith("# Children\n\nBefore.\n\n")
    assert "stale.md" not in plan.after
    assert "epic-migration.md" in plan.after


def test_shared_reconciler_is_idempotent_across_repeated_calls() -> None:
    before = "# Children\n\nSee /new/path.md.\n\n# Items\n\n- [stale](stale.md) — open · design\n"
    entries = ("- [current](current.md) — open · design",)

    once = reconcile_entries(before, entries)
    twice = reconcile_entries(once, entries)

    assert "See /new/path.md." in once
    assert "- [current](current.md) — open · design" in once
    assert "stale.md" not in once
    assert twice == once


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


def test_parse_entry_reads_the_shapes_a_title_can_take() -> None:
    # Balanced brackets are valid CommonMark link text and must be read back.
    assert parse_entry("- [TechDebt: plain](target.md) — open · execute") == "target.md"
    assert parse_entry("- [TechDebt: Replace [[entities/x]] syntax](target.md) — open · done") == "target.md"
    assert parse_entry("- [Bug: fix [gw work] parsing](target.md) — open · design") == "target.md"
    # Backslash-escaped brackets are what `render_entry` emits after Task 2.
    assert parse_entry(r"- [Bug: fix a\] title](target.md) — open · design") == "target.md"
    assert parse_entry(r"- [Bug: a \]\( b](target.md) — open · design") == "target.md"
    # Indented and multi-space bullets stay readable.
    assert parse_entry("  -   [Bug: indented](target.md) — open · design") == "target.md"


def test_parse_entry_declines_what_is_not_an_entry() -> None:
    assert parse_entry("") is None
    assert parse_entry("Just prose.") is None
    assert parse_entry("- not a link at all") is None
    assert parse_entry("- [unterminated link text](target.md") is None
    assert parse_entry("- [no destination]") is None
    assert parse_entry("- [empty destination]()") is None
    # An unbalanced `]` is not a link under CommonMark, so it is not an entry.
    assert parse_entry("- [Bug: fix a] title](target.md) — open · design") is None
    assert parse_entry("- [abc") is None  # no `]` at all
    assert parse_entry("- [abc\\") is None  # trailing escape runs off the end


def test_render_entry_escapes_brackets_so_the_line_is_always_a_link() -> None:
    item = make_item("work/bug-bracket", type="Bug", title=r"fix a] title \ here")
    line = render_entry(item)
    assert r"\]" in line
    assert r"\\" in line
    assert parse_entry(line) == "bug-bracket.md"


def test_render_entry_round_trips_through_parse_entry() -> None:
    titles = (
        "plain title",
        "Replace [[entities/x]] syntax",
        "fix [gw work] parsing",
        "fix a] title",
        "fix a[ title",
        "a ]( b",
        r"a \ backslash",
    )
    for title in titles:
        item = make_item("work/bug-round-trip", type="Bug", title=title)
        assert parse_entry(render_entry(item)) == "bug-round-trip.md", title


def test_render_entry_leaves_a_readable_title_unescaped() -> None:
    item = make_item("work/bug-code-span", type="TechDebt", title="Replace `[[entities/x]]` syntax")
    line = render_entry(item)
    assert "\\[" not in line and "\\]" not in line
    assert parse_entry(line) == "bug-code-span.md"


def test_render_entry_produces_a_readable_line_for_every_title() -> None:
    # Every line `render_entry` produces is readable: the plain form is used
    # whenever it already reads back correctly, and escaping only kicks in
    # when it does not. Covers a plain title, a code-span title, balanced
    # brackets, an unbalanced `]`, an unbalanced `[`, a literal `](`, and a
    # backslash.
    titles = (
        "plain title",
        "Replace `[[entities/x]]` syntax",
        "Replace [[entities/x]] syntax",
        "fix a] title",
        "fix a[ title",
        "a ]( b",
        r"a \ backslash",
    )
    for title in titles:
        item = make_item("work/bug-readable", type="Bug", title=title)
        line = render_entry(item)
        assert parse_entry(line) == "bug-readable.md", title

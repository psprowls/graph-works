from pathlib import Path

from work_helpers import lane_report, write_item


def _codes(root: Path) -> set[str]:
    return {finding.code for finding in lane_report(root).errors}


def test_release_below_an_owner_and_children_below_a_leaf_are_errors(tmp_path: Path) -> None:
    release = "work/release-parent"
    nested_release = f"{release}/children/release-nested"
    leaf = f"{release}/children/bug-leaf"
    write_item(tmp_path, release, "type: Release\nwork_status: open\n")
    write_item(tmp_path, nested_release, "type: Release\nwork_status: open\n")
    write_item(tmp_path, leaf, "type: Bug\nwork_status: open\n")
    (tmp_path / leaf / "children").mkdir(parents=True)

    codes = _codes(tmp_path)

    assert "structure.release-nested" in codes
    assert "structure.children-on-leaf" in codes


def test_prefix_type_mismatch_and_illegal_lane_are_reported(tmp_path: Path) -> None:
    write_item(tmp_path, "work/bug-named-like-a-feature", "type: Feature\nwork_status: open\n")
    illegal = tmp_path / "work" / "release-root" / "epic-skips-children.md"
    illegal.parent.mkdir(parents=True)
    illegal.write_text("---\ntype: Epic\ntitle: T\ndescription: D\nwork_status: open\n---\n", encoding="utf-8")

    codes = {finding.code for finding in lane_report(tmp_path).findings}

    assert "structure.prefix-type-mismatch" in codes
    assert "structure.illegal-lane" in codes


def test_parent_capable_item_without_its_owned_directory_is_reported(tmp_path: Path) -> None:
    write_item(tmp_path, "work/release-cutover", "type: Release\nwork_status: open\n")

    assert "structure.owned-directory-missing" in _codes(tmp_path)


def test_child_lane_beneath_a_missing_owner_is_illegal(tmp_path: Path) -> None:
    write_item(tmp_path, "work/missing/children/bug-orphan", "type: Bug\nwork_status: open\n")

    assert "structure.illegal-lane" in _codes(tmp_path)


def test_duplicate_source_ids_are_a_per_item_error(tmp_path: Path) -> None:
    item = "work/feature-import"
    write_item(
        tmp_path,
        item,
        "type: Feature\nwork_status: open\nsources:\n"
        "  - id: design\n    resource: /work/feature-import/references/01-design.md\n"
        "  - id: design\n    resource: /work/feature-import/references/01-design-copy.md\n",
    )
    references = tmp_path / item / "references"
    references.mkdir(parents=True)
    (references / "01-design.md").write_text("design\n", encoding="utf-8")
    (references / "01-design-copy.md").write_text("copy\n", encoding="utf-8")

    assert "structure.source-id-duplicate" in _codes(tmp_path)


def test_source_traversal_and_symlink_escape_are_rejected(tmp_path: Path) -> None:
    item = "work/feature-import"
    outside = tmp_path.parent / "outside-source.md"
    outside.write_text("outside\n", encoding="utf-8")
    references = tmp_path / item / "references"
    references.mkdir(parents=True)
    (references / "linked.md").symlink_to(outside)
    write_item(
        tmp_path,
        item,
        "type: Feature\nwork_status: open\nsources:\n"
        "  - id: traversal\n    resource: /work/feature-import/references/../../../outside-source.md\n"
        "  - id: linked\n    resource: /work/feature-import/references/linked.md\n",
    )

    findings = lane_report(tmp_path).by_code("structure.source-escape")

    assert len(findings) == 2
    assert all(finding.severity == "error" for finding in findings)


def test_owned_source_that_is_absent_is_reported(tmp_path: Path) -> None:
    item = "work/feature-import"
    write_item(
        tmp_path,
        item,
        "type: Feature\nwork_status: open\nsources:\n"
        "  - id: design\n    resource: /work/feature-import/references/01-design.md\n",
    )
    (tmp_path / item).mkdir(parents=True, exist_ok=True)

    assert "structure.source-missing" in {finding.code for finding in lane_report(tmp_path).findings}


def test_owned_source_that_is_a_dangling_symlink_is_reported_missing(tmp_path: Path) -> None:
    item = "work/feature-import"
    write_item(
        tmp_path,
        item,
        "type: Feature\nwork_status: open\nsources:\n"
        "  - id: design\n    resource: /work/feature-import/references/01-design.md\n",
    )
    references = tmp_path / item / "references"
    references.mkdir(parents=True)
    (references / "01-design.md").symlink_to("missing.md")

    findings = lane_report(tmp_path).by_code("structure.source-missing")

    assert len(findings) == 1
    assert findings[0].path == f"{item}.md"


def test_index_reports_missing_stale_duplicate_and_non_direct_entries(tmp_path: Path) -> None:
    release = "work/release-cutover"
    lane = f"{release}/children"
    epic = f"{lane}/epic-migration"
    descendant = f"{epic}/children/feature-import"
    write_item(tmp_path, release, "type: Release\nwork_status: open\n")
    write_item(tmp_path, epic, "type: Epic\nwork_status: open\n")
    write_item(tmp_path, descendant, "type: Feature\nwork_status: open\n")
    index = tmp_path / lane / "index.md"
    index.write_text(
        "<!-- graph-works:work-items:start -->\n"
        "- [Stale](gone.md)\n"
        "- [Nested](epic-migration/children/feature-import.md)\n"
        "- [Nested again](epic-migration/children/feature-import.md)\n"
        "<!-- graph-works:work-items:end -->\n",
        encoding="utf-8",
    )

    codes = {finding.code for finding in lane_report(tmp_path).findings}

    assert {
        "structure.index-entry-missing",
        "structure.index-entry-stale",
        "structure.index-entry-duplicate",
        "structure.index-entry-non-direct",
    } <= codes


def test_a_bracketed_title_is_not_reported_missing(path_native_root: Path) -> None:
    """D1: a balanced bracket in a title is valid link text the rule must read."""
    from work_tracker_okf.indexes import GENERATED_END, GENERATED_START

    write_item(
        path_native_root,
        "work/bug-bracket-title",
        "type: Bug\ntitle: 'Replace [[entities/x]] syntax'\nwork_status: open\n",
    )
    lane_index = path_native_root / "work" / "index.md"
    entry = "- [Bug: Replace [[entities/x]] syntax](bug-bracket-title.md) — open · not started"
    lane_index.write_text(f"{GENERATED_START}\n{entry}\n{GENERATED_END}\n", encoding="utf-8")

    codes = [f.code for f in lane_report(path_native_root).findings if "bug-bracket-title" in (f.message or "")]
    assert "structure.index-entry-missing" not in codes


def test_an_unreadable_region_line_is_reported_not_dropped(path_native_root: Path) -> None:
    from work_tracker_okf.indexes import GENERATED_END, GENERATED_START

    lane_index = path_native_root / "work" / "index.md"
    lane_index.write_text(
        f"{GENERATED_START}\n- [Bug: a] broken](nowhere.md) — open · design\n{GENERATED_END}\n",
        encoding="utf-8",
    )

    codes = [f.code for f in lane_report(path_native_root).findings]
    assert "structure.index-entry-unreadable" in codes


def test_a_blank_line_in_the_region_is_not_reported(path_native_root: Path) -> None:
    """The region body starts with a newline, so `splitlines` always yields a
    leading empty string. Reporting it would fire on every well-formed index."""
    from work_tracker_okf.indexes import GENERATED_END, GENERATED_START

    lane_index = path_native_root / "work" / "index.md"
    lane_index.write_text(f"{GENERATED_START}\n\n{GENERATED_END}\n", encoding="utf-8")

    codes = [f.code for f in lane_report(path_native_root).findings]
    assert "structure.index-entry-unreadable" not in codes

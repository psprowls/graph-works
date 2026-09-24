from pathlib import Path

from work_helpers import lane_report, load_written_items, write_item
from work_tracker_okf._rules.structure import _direct_entries


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
        "# Items\n\n"
        "- [Stale](gone.md)\n"
        "- [Nested](epic-migration/children/feature-import.md)\n"
        "- [Nested again](epic-migration/children/feature-import.md)\n"
        "- [children/](children/index.md)\n",
        encoding="utf-8",
    )

    findings = lane_report(tmp_path).findings
    codes = {finding.code for finding in findings}

    assert {
        "structure.index-entry-missing",
        "structure.index-entry-stale",
        "structure.index-entry-duplicate",
        "structure.index-entry-non-direct",
    } <= codes
    assert not any("children/index.md" in (finding.message or "") for finding in findings)


def test_a_bracketed_title_is_not_reported_missing(path_native_root: Path) -> None:
    """D1: a balanced bracket in a title is valid link text the rule must read."""
    write_item(
        path_native_root,
        "work/bug-bracket-title",
        "type: Bug\ntitle: 'Replace [[entities/x]] syntax'\nwork_status: open\n",
    )
    lane_index = path_native_root / "work" / "index.md"
    entry = "- [Bug: Replace [[entities/x]] syntax](bug-bracket-title.md) — open · not started"
    lane_index.write_text(f"# Items\n\n{entry}\n", encoding="utf-8")

    codes = [f.code for f in lane_report(path_native_root).findings if "bug-bracket-title" in (f.message or "")]
    assert "structure.index-entry-missing" not in codes


def test_an_unreadable_region_line_is_reported_not_dropped(path_native_root: Path) -> None:
    lane_index = path_native_root / "work" / "index.md"
    lane_index.write_text(
        "# Items\n\n- [Bug: a] broken](nowhere.md) — open · design\n",
        encoding="utf-8",
    )

    codes = [f.code for f in lane_report(path_native_root).findings]
    assert "structure.index-entry-unreadable" in codes


def test_a_blank_line_in_the_region_is_not_reported(path_native_root: Path) -> None:
    """A blank line between entries is not an attempted entry and must not be
    reported as unreadable."""
    lane_index = path_native_root / "work" / "index.md"
    lane_index.write_text("# Items\n\n\n", encoding="utf-8")

    codes = [f.code for f in lane_report(path_native_root).findings]
    assert "structure.index-entry-unreadable" not in codes


def test_a_legacy_nested_archive_page_is_an_illegal_lane_not_a_crash(tmp_path: Path) -> None:
    write_item(tmp_path, "work/epic-live", "type: Epic\nwork_status: open\n")
    (tmp_path / "work/epic-live/children").mkdir(parents=True, exist_ok=True)
    legacy = tmp_path / "work/epic-live/children/_archive/bug-old.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "---\ntitle: T\ndescription: D\ntype: Bug\nwork_status: resolved\n---\n",
        encoding="utf-8",
    )

    report = lane_report(tmp_path)

    illegal = report.by_code("structure.illegal-lane")
    assert [finding.path for finding in illegal] == ["work/epic-live/children/_archive/bug-old.md"]
    assert "work/epic-live" in {item.path for item in load_written_items(tmp_path)}


def _index_findings(root: Path) -> list[tuple[str, str]]:
    return [
        (finding.code, finding.message or "")
        for finding in lane_report(root).findings
        if finding.code.startswith("structure.index-entry-")
    ]


def test_a_prose_link_to_a_non_item_page_is_not_an_index_entry(tmp_path: Path) -> None:
    """A lane index may link a design doc or any other non-item page from a
    bullet; only a bare direct-item target or a real work-item page is an
    entry the rule classifies, so this is neither stale nor anything else."""
    epic = "work/epic-x"
    write_item(tmp_path, epic, "type: Epic\nwork_status: open\n")
    references = tmp_path / epic / "references"
    references.mkdir(parents=True)
    (references / "01-design.md").write_text("# Design\n", encoding="utf-8")
    (tmp_path / "work" / "index.md").write_text(
        "# Items\n\n"
        "- [Epic: T](epic-x.md) — open · not started\n\n"
        "# Reading\n\n"
        "- [Design](epic-x/references/01-design.md)\n",
        encoding="utf-8",
    )

    assert _index_findings(tmp_path) == []


def test_a_task_list_bullet_is_not_reported_unreadable(tmp_path: Path) -> None:
    write_item(tmp_path, "work/bug-x", "type: Bug\nwork_status: open\n")
    (tmp_path / "work" / "index.md").write_text(
        "# Items\n\n"
        "- [Bug: T](bug-x.md) — open · not started\n\n"
        "# Todo\n\n"
        "- [ ] triage the backlog\n"
        "- [x] archive the resolved epics\n"
        "- [X] done, capitalised\n"
        "- [ ]\n",
        encoding="utf-8",
    )

    assert _index_findings(tmp_path) == []


def test_direct_entries_ignores_a_non_link_bullet() -> None:
    text = "- just a note, no link here\n"
    targets, unreadable = _direct_entries(text, "work", frozenset())
    assert targets == ()
    assert unreadable == ()


def test_direct_entries_ignores_a_subdirectory_link() -> None:
    text = "- [children/](children/index.md)\n"
    targets, unreadable = _direct_entries(text, "work", frozenset())
    assert targets == ()
    assert unreadable == ()


def test_direct_entries_reports_a_malformed_item_link_as_unreadable() -> None:
    text = "- [Broken(epic-x.md) — open · design\n"
    targets, unreadable = _direct_entries(text, "work", frozenset())
    assert targets == ()
    assert len(unreadable) == 1


def test_direct_entries_finds_item_links_anywhere_in_the_file() -> None:
    text = (
        "Some notes.\n\n"
        "- [Epic: X](epic-x.md) — open · design\n\n"
        "# Subdirectories\n\n"
        "- [children/](children/index.md)\n"
    )
    targets, unreadable = _direct_entries(text, "work", frozenset())
    assert targets == ("epic-x.md",)
    assert unreadable == ()


def test_direct_entries_does_not_mistake_a_slug_ending_in_index_for_an_index_page() -> None:
    """The index-page exclusion is a basename check, not a suffix match: a real
    item slug that happens to end in "index" is not an index page."""
    text = "- [Feature: Regen index](feature-regen-index.md) — open · design\n"
    targets, unreadable = _direct_entries(text, "work", frozenset())
    assert targets == ("feature-regen-index.md",)
    assert unreadable == ()

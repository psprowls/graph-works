from pathlib import Path
from types import MappingProxyType

import pytest
from okf_io import load_bundle
from work_helpers import load_written_items, make_item, write_item
from work_tracker_okf.mutation import (
    DirectoryPrecondition,
    MutationRefusal,
    WorkMutationPlan,
    _add_absent_mkdir_ancestors,
    _collision_refusals,
    _directory_collision_refusals,
    _directory_preconditions,
    _future_items,
    _lane_mapping,
    _member_mapping,
    _opaque_warnings,
    _plan_path_mutation,
    _prefix_rebase,
    _rebase_item_sources,
    _reconciliation_preimage,
    _resolved_inside,
    _restore_reserved_text,
    _subtree_items,
    directory_manifest_digest,
)


def test_a_refusal_makes_the_unified_plan_not_ok(tmp_path: Path) -> None:
    plan = WorkMutationPlan(
        root=tmp_path,
        operation="reparent",
        path_mapping={},
        move_plan=None,
        moves=(),
        writes=(),
        deletes=(),
        mkdirs=(),
        warnings=(),
        refusals=(MutationRefusal("work/source", "unknown-source", "missing"),),
        validate_paths=(),
    )

    assert not plan.ok


def test_the_unified_plan_is_frozen(tmp_path: Path) -> None:
    plan = WorkMutationPlan(
        root=tmp_path,
        operation="indexes",
        path_mapping={},
        move_plan=None,
        moves=(),
        writes=(),
        deletes=(),
        mkdirs=(),
        warnings=(),
        refusals=(),
        validate_paths=(),
    )

    with pytest.raises(AttributeError):
        plan.operation = "archive"  # type: ignore[misc]


def test_directory_preconditions_are_frozen_and_default_compatibly(tmp_path: Path) -> None:
    condition = DirectoryPrecondition("work/source", "digest")
    plan = WorkMutationPlan(
        root=tmp_path,
        operation="reparent",
        path_mapping={},
        move_plan=None,
        moves=(),
        writes=(),
        deletes=(),
        mkdirs=(),
        warnings=(),
        refusals=(),
        validate_paths=(),
    )

    assert plan.directory_preconditions == ()
    with pytest.raises(AttributeError):
        condition.member = "work/changed"  # type: ignore[misc]


def test_directory_manifest_digest_tracks_files_directories_and_symlinks(tmp_path: Path) -> None:
    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "payload.txt").write_bytes(b"one")
    (owned / "link").symlink_to("payload.txt")
    before = directory_manifest_digest(owned)
    (owned / "payload.txt").write_bytes(b"two")
    assert directory_manifest_digest(owned) != before


def test_mutation_mapping_helpers_cover_missing_cycles_and_owned_members(tmp_path: Path) -> None:
    root = make_item(
        "work/epic",
        type="Epic",
        child_paths=("work/feature", "work/missing"),
    )
    child = make_item(
        "work/feature",
        parent_path=root.path,
        ancestor_paths=(root.path,),
        child_paths=(root.path,),
    )
    assert [item.path for item in _subtree_items((root, child), (root.path, "work/unknown"))] == [
        root.path,
        child.path,
    ]

    (tmp_path / "work/epic/references").mkdir(parents=True)
    (tmp_path / "work/epic.md").write_text("# Epic\n", encoding="utf-8")
    (tmp_path / "work/epic/references/note.bin").write_bytes(b"opaque")
    (tmp_path / "work/index.md").write_text("# Work\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)
    assert _member_mapping(bundle, {root.path: "work/moved"}) == {
        "work/epic.md": "work/moved.md",
        "work/epic/references/note.bin": "work/moved/references/note.bin",
    }


def test_future_item_and_lane_mappings_rewrite_all_structural_paths() -> None:
    root = make_item(
        "work/epic",
        type="Epic",
        child_paths=("work/feature", "work/_archive/bug"),
    )
    child = make_item("work/feature", type="Bug", parent_path=root.path, ancestor_paths=(root.path,))
    mapping = {root.path: "work/_archive/epic", child.path: "work/_archive/epic/children/feature"}
    future = _future_items((root, child), mapping)
    assert future[0].archived
    assert future[1].parent_path == "work/_archive/epic"
    assert future[1].archived
    assert _lane_mapping((root, child), mapping) == {"work/epic/children": "work/_archive/epic/children"}
    assert _lane_mapping((child,), {}) == {}


def test_collision_and_directory_precondition_helpers_report_conflicts(tmp_path: Path) -> None:
    (tmp_path / "existing").mkdir()
    (tmp_path / "existing/file").write_text("x", encoding="utf-8")
    member_refusals = _collision_refusals(
        tmp_path,
        {"source-a": "existing/file", "source-b": "existing/file"},
    )
    directory_refusals = _directory_collision_refusals(
        tmp_path,
        {"source-a": "existing", "source-b": "existing"},
    )
    assert {refusal.kind for refusal in member_refusals} == {"dest-exists"}
    assert {refusal.kind for refusal in directory_refusals} == {"directory-dest-exists"}

    conditions, refusals = _directory_preconditions(
        tmp_path,
        {"existing": "destination"},
        {"missing": "existing"},
    )
    assert {condition.member for condition in conditions} == {"destination", "existing", "missing"}
    assert {refusal.kind for refusal in refusals} == {"directory-dest-exists"}
    complete = _add_absent_mkdir_ancestors(
        tmp_path,
        ("new/deep/path",),
        MappingProxyType({condition.member: condition for condition in conditions}),
    )
    assert {condition.member for condition in complete} >= {"new", "new/deep", "new/deep/path"}


def test_warning_resolution_and_reserved_text_helpers(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("mentions work/old", encoding="utf-8")
    binary = tmp_path / "binary.bin"
    binary.write_bytes(b"\xff")
    link = tmp_path / "link"
    link.symlink_to(note)
    assert _opaque_warnings(tmp_path, ("note.md", "binary.bin", "link"), ("work/old",)) == (
        "note.md: opaque UTF-8 content mentions old canonical path 'work/old'; bytes remain unchanged",
    )
    assert _resolved_inside(tmp_path, note)
    assert not _resolved_inside(tmp_path, tmp_path.parent / "outside")
    assert (
        _restore_reserved_text(
            "dir/virtual.md virtual.md virtual",
            {"dir/virtual.md": "dir/index.md"},
            {},
        )
        == "dir/index.md index.md index"
    )


def test_prefix_rebase_rewrites_a_dangling_root_absolute_resource() -> None:
    mapping = {"work/bug-old": "work/_archive/bug-old"}

    result = _prefix_rebase("/work/bug-old/references/01-design.md", mapping)

    assert result == "/work/_archive/bug-old/references/01-design.md"


def test_prefix_rebase_matches_on_path_boundaries() -> None:
    mapping = {"work/bug-a": "work/_archive/bug-a"}

    assert _prefix_rebase("/work/bug-alpha/references/01-design.md", mapping) is None


def test_prefix_rebase_prefers_the_deepest_mapping() -> None:
    mapping = {
        "work/epic": "work/_archive/epic",
        "work/epic/children/bug": "work/_archive/epic/children/bug",
    }

    result = _prefix_rebase("/work/epic/children/bug/references/01-design.md", mapping)

    assert result == "/work/_archive/epic/children/bug/references/01-design.md"


def test_prefix_rebase_returns_none_for_relative_and_external_resources() -> None:
    mapping = {"work/bug-old": "work/_archive/bug-old"}

    assert _prefix_rebase("references/01-design.md", mapping) is None
    assert _prefix_rebase("https://example.com/x", mapping) is None
    assert _prefix_rebase("report:v2.sql", mapping) is None


def test_prefix_rebase_returns_none_when_already_correct() -> None:
    mapping = {"work/bug-old": "work/_archive/bug-old"}

    assert _prefix_rebase("/work/_archive/bug-old/references/01-design.md", mapping) is None


def test_prefix_rebase_returns_none_for_an_exact_item_page_match_outside_mapping() -> None:
    mapping = {"work/bug-old": "work/_archive/bug-old"}

    assert _prefix_rebase("/work/other-bug.md", mapping) is None


_PAGE_WITH_DANGLING_SOURCE = (
    b"---\n"
    b"type: Bug\n"
    b"title: T\n"
    b"description: D\n"
    b"sources:\n"
    b"  - id: design\n"
    b"    resource: /work/bug-old/references/01-design.md\n"
    b"    title: Design\n"
    b"status: stable\n"
    b"work_status: open\n"
    b"opened: 2026-08-31\n"
    b"updated: 2026-08-31\n"
    b"phase: execute\n"
    b"---\n"
    b"\n## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n"
)


def test_rebase_item_sources_rewrites_a_dangling_pointer() -> None:
    mapping = {"work/bug-old": "work/_archive/bug-old"}

    after = _rebase_item_sources(_PAGE_WITH_DANGLING_SOURCE, mapping)

    assert b"/work/_archive/bug-old/references/01-design.md" in after
    assert b"/work/bug-old/references/01-design.md" not in after


def test_rebase_item_sources_is_a_byte_identity_no_op_when_nothing_matches() -> None:
    mapping = {"work/some-other-item": "work/_archive/some-other-item"}

    after = _rebase_item_sources(_PAGE_WITH_DANGLING_SOURCE, mapping)

    assert after == _PAGE_WITH_DANGLING_SOURCE


def test_rebase_item_sources_leaves_unrelated_frontmatter_untouched() -> None:
    mapping = {"work/bug-old": "work/_archive/bug-old"}

    after = _rebase_item_sources(_PAGE_WITH_DANGLING_SOURCE, mapping)

    assert b"title: T\n" in after
    assert b"phase: execute\n" in after


def test_dangling_stamped_resource_is_rebased_by_the_planner(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "work/bug-dangling",
        "type: Bug\nwork_status: resolved\nphase: execute\n"
        "sources:\n"
        "  - id: design\n"
        "    resource: /work/bug-dangling/references/01-design.md\n"
        "    title: Design\n",
    )
    bundle = load_bundle(tmp_path)
    items = load_written_items(tmp_path)

    plan = _plan_path_mutation(
        bundle,
        items,
        "archive",
        {"work/bug-dangling": "work/_archive/bug-dangling"},
        roots=("work/bug-dangling",),
    )

    assert plan.ok, plan.refusals
    page_write = next(write for write in plan.writes if write.member == "work/_archive/bug-dangling.md")
    assert b"/work/_archive/bug-dangling/references/01-design.md" in page_write.after
    assert b"/work/bug-dangling/references/01-design.md" not in page_write.after


def test_planned_bytes_match_whether_or_not_the_artifact_exists(tmp_path: Path) -> None:
    frontmatter = (
        "type: Bug\nwork_status: resolved\nphase: execute\n"
        "sources:\n"
        "  - id: design\n"
        "    resource: /work/bug-dangling/references/01-design.md\n"
        "    title: Design\n"
    )
    write_item(tmp_path, "work/bug-dangling", frontmatter)
    bundle = load_bundle(tmp_path)
    items = load_written_items(tmp_path)
    plan_without_artifact = _plan_path_mutation(
        bundle,
        items,
        "archive",
        {"work/bug-dangling": "work/_archive/bug-dangling"},
        roots=("work/bug-dangling",),
    )

    (tmp_path / "work/bug-dangling/references").mkdir(parents=True)
    (tmp_path / "work/bug-dangling/references/01-design.md").write_text(
        "---\ntitle: T\ndescription: D\n---\n", encoding="utf-8"
    )
    bundle_with_artifact = load_bundle(tmp_path)
    items_with_artifact = load_written_items(tmp_path)
    plan_with_artifact = _plan_path_mutation(
        bundle_with_artifact,
        items_with_artifact,
        "archive",
        {"work/bug-dangling": "work/_archive/bug-dangling"},
        roots=("work/bug-dangling",),
    )

    page_without = next(w for w in plan_without_artifact.writes if w.member == "work/_archive/bug-dangling.md")
    page_with = next(w for w in plan_with_artifact.writes if w.member == "work/_archive/bug-dangling.md")
    assert page_without.after == page_with.after


def test_reparenting_across_lanes_reconciles_by_link_target_not_markers(tmp_path: Path) -> None:
    write_item(tmp_path, "work/bug-dangling", "type: Bug\nwork_status: resolved\nphase: execute\n")
    (tmp_path / "work/index.md").write_text(
        "# Work\n\nHuman prose about this lane, with no markers at all.\n", encoding="utf-8"
    )
    bundle = load_bundle(tmp_path)
    items = load_written_items(tmp_path)

    plan = _plan_path_mutation(
        bundle,
        items,
        "archive",
        {"work/bug-dangling": "work/_archive/bug-dangling"},
        roots=("work/bug-dangling",),
    )

    assert plan.ok, plan.refusals
    by_member = {write.member: write for write in plan.writes}
    destination_index = by_member["work/_archive/index.md"].after.decode("utf-8")
    assert "graph-works:work-items" not in destination_index
    assert "bug-dangling.md" in destination_index


def test_archiving_prunes_the_vacated_entry_from_the_source_lane_index(tmp_path: Path) -> None:
    """A moved item's own entry in the source lane's index must be pruned, not
    relinked in place. The generic okf-ext rewrite retargets a moved item's own
    stale link (``bug-done.md`` -> ``_archive/bug-done.md``) before
    `_index_effects` reads the source index's pre-image; sourcing that pre-image
    from the rewrite's output instead of the true on-disk bytes let the
    relinked, now-cross-lane-shaped entry survive `reconcile_entries` forever,
    since it no longer looks like a bare, prunable direct-entry target."""
    write_item(tmp_path, "work/bug-done", "type: Bug\nwork_status: resolved\nphase: execute\n")
    (tmp_path / "work/index.md").write_text(
        "# Work\n\n- [Bug: Done](bug-done.md) — resolved · execute\n", encoding="utf-8"
    )
    bundle = load_bundle(tmp_path)
    items = load_written_items(tmp_path)

    plan = _plan_path_mutation(
        bundle,
        items,
        "archive",
        {"work/bug-done": "work/_archive/bug-done"},
        roots=("work/bug-done",),
    )

    assert plan.ok, plan.refusals
    by_member = {write.member: write for write in plan.writes}
    source_index = by_member["work/index.md"].after.decode("utf-8")
    assert "bug-done" not in source_index


def test_reconciliation_preimage_takes_entries_raw_and_everything_else_rewritten() -> None:
    raw = b"# Work\n\nSee [x](epic-x/index.md).\n\n- [Epic: X](epic-x.md) \xe2\x80\x94 resolved \xc2\xb7 done\n"
    rewritten = (
        b"# Work\n\nSee [x](_archive/epic-x/index.md).\n\n"
        b"- [Epic: X](_archive/epic-x.md) \xe2\x80\x94 resolved \xc2\xb7 done\n"
    )

    merged = _reconciliation_preimage(raw, rewritten)

    assert merged == (
        b"# Work\n\nSee [x](_archive/epic-x/index.md).\n\n- [Epic: X](epic-x.md) \xe2\x80\x94 resolved \xc2\xb7 done\n"
    )


def test_reconciliation_preimage_falls_back_to_raw_bytes_when_it_cannot_pair_lines() -> None:
    raw = b"# Work\n\n- [Epic: X](epic-x.md)\n"

    assert _reconciliation_preimage(raw, None) == raw
    assert _reconciliation_preimage(None, b"anything\n") is None
    assert _reconciliation_preimage(raw, b"# Work\n- [Epic: X](_archive/epic-x.md)\n") == raw
    assert _reconciliation_preimage(raw, b"# Work\n\n\xff\n") == raw

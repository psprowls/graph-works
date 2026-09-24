from datetime import date
from pathlib import Path

import pytest
from okf_io import load_bundle
from work_helpers import make_item, write_item
from work_tracker_okf.archive import _archive_subtree_mapping, _default_targets, plan_archive
from work_tracker_okf.cli import _apply_mutation
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.mutation import _plan_path_mutation


def _terminal_tree(root: Path, *, child_status: str = "resolved") -> tuple[str, str, str]:
    release = "work/release-cutover"
    epic = "work/epic-migration"
    feature = f"{epic}/children/feature-done"
    write_item(root, release, "type: Release\nwork_status: open\n")
    write_item(root, epic, "type: Epic\nwork_status: resolved\n")
    write_item(root, feature, f"type: Feature\nwork_status: {child_status}\n")
    (root / release).mkdir(parents=True, exist_ok=True)
    (root / epic / "children").mkdir(parents=True, exist_ok=True)
    return release, epic, feature


def _terminal_leaf(root: Path, name: str = "work/bug-done") -> str:
    write_item(root, name, "type: Bug\nwork_status: resolved\n")
    return name


def _snapshot(root: Path) -> dict[str, tuple[str, bytes | str]]:
    snapshot: dict[str, tuple[str, bytes | str]] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir(), reverse=True):
            member = path.relative_to(root).as_posix()
            if path.is_symlink():
                snapshot[member] = ("symlink", path.readlink().as_posix())
            elif path.is_dir():
                snapshot[member] = ("directory", b"")
                pending.append(path)
            else:
                snapshot[member] = ("file", path.read_bytes())
    return snapshot


def test_a_child_target_is_refused_as_not_top_level(tmp_path: Path) -> None:
    _release, epic, feature = _terminal_tree(tmp_path)
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), (feature,))

    assert not plan.ok
    refusal = next(refusal for refusal in plan.refusals if refusal.kind == "not-top-level")
    assert refusal.path == feature
    assert refusal.detail == (f"only top-level items are archived; children move with their root — archive {epic}")
    assert plan.writes == plan.moves == plan.deletes == ()


def test_a_top_level_leaf_archives_to_the_root_archive_lane(tmp_path: Path) -> None:
    leaf = _terminal_leaf(tmp_path)
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), (leaf,))

    assert plan.ok, plan.refusals
    assert plan.path_mapping == {leaf: "work/_archive/bug-done"}


def test_naming_a_root_and_its_child_refuses_the_child_as_not_top_level(tmp_path: Path) -> None:
    _release, epic, feature = _terminal_tree(tmp_path)
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), (epic, feature))

    assert not plan.ok
    assert {(refusal.path, refusal.kind) for refusal in plan.refusals} == {(feature, "not-top-level")}
    assert plan.writes == plan.moves == plan.deletes == ()


def test_an_archived_descendant_reports_already_archived_not_not_top_level(tmp_path: Path) -> None:
    child = "work/_archive/epic-old/children/bug-old"
    write_item(tmp_path, "work/_archive/epic-old", "type: Epic\nwork_status: resolved\n")
    write_item(tmp_path, child, "type: Bug\nwork_status: resolved\n")
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), (child,))

    assert [refusal.kind for refusal in plan.refusals] == ["already-archived"]


def test_sweep_skips_a_root_with_an_open_descendant_and_archives_a_sibling_root(tmp_path: Path) -> None:
    held = "work/epic-held"
    write_item(tmp_path, held, "type: Epic\nwork_status: resolved\n")
    write_item(tmp_path, f"{held}/children/bug-open", "type: Bug\nwork_status: open\n")
    (tmp_path / held / "children").mkdir(parents=True, exist_ok=True)
    leaf = _terminal_leaf(tmp_path)
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), None)

    assert plan.ok, plan.refusals
    assert plan.path_mapping == {leaf: "work/_archive/bug-done"}


def test_round_trip_reload_after_archiving_a_three_level_root(tmp_path: Path) -> None:
    epic = "work/epic-three"
    feature = f"{epic}/children/feature-mid"
    bug = f"{feature}/children/bug-leaf"
    write_item(tmp_path, epic, "type: Epic\nwork_status: resolved\n")
    write_item(tmp_path, feature, "type: Feature\nwork_status: resolved\n")
    write_item(tmp_path, bug, "type: Bug\nwork_status: wontfix\n")
    (tmp_path / feature / "children").mkdir(parents=True, exist_ok=True)
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), None)
    assert plan.ok, plan.refusals
    _apply_mutation(plan)

    reprojected = {item.path: item for item in load_items(load_bundle(tmp_path, ignore=IGNORE))}
    moved_bug = "work/_archive/epic-three/children/feature-mid/children/bug-leaf"
    assert reprojected[moved_bug].parent_path == "work/_archive/epic-three/children/feature-mid"
    assert all(item.archived for item in reprojected.values())
    assert not any("/children/_archive" in path for path in reprojected)
    assert not list(tmp_path.rglob("_archive/*/children/_archive"))


def test_parent_archive_normalizes_terminal_descendants_before_moving_parent(tmp_path: Path) -> None:
    _release, epic, feature = _terminal_tree(tmp_path)
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), (epic,))

    assert plan.ok, plan.refusals
    assert plan.path_mapping[epic] == "work/_archive/epic-migration"
    assert plan.path_mapping[feature] == "work/_archive/epic-migration/children/feature-done"


def test_parent_archive_prunes_its_entry_and_rebases_the_lanes_other_links(tmp_path: Path) -> None:
    """One lane index, both jobs, one pass: the archived parent's own entry is
    pruned (judged against its raw, un-rewritten target), while the same
    index's okf-io subdirectory link and a human prose link into the moved
    subtree follow it to the archive (taken from the okf-ext rewrite) instead
    of being left pointing at the vacated location."""
    _release, epic, _feature = _terminal_tree(tmp_path)
    (tmp_path / epic / "index.md").write_text("# Epic migration\n", encoding="utf-8")
    (tmp_path / "work/index.md").write_text(
        "# Work\n"
        "\n"
        "Start with [the import feature](epic-migration/children/feature-done.md).\n"
        "\n"
        "# Items\n"
        "\n"
        "- [Epic: T](epic-migration.md) — resolved · not started\n"
        "- [Release: T](release-cutover.md) — open · not started\n"
        "\n"
        "# Subdirectories\n"
        "\n"
        "- [epic-migration](epic-migration/index.md)\n",
        encoding="utf-8",
    )
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), (epic,))

    assert plan.ok, plan.refusals
    assert plan.path_mapping[epic] == "work/_archive/epic-migration"
    by_member = {write.member: write for write in plan.writes}
    source_index = by_member["work/index.md"].after.decode("utf-8")
    assert "(epic-migration.md)" not in source_index
    assert "_archive/epic-migration.md" not in source_index
    assert "- [Release: T](release-cutover.md) — open · not started" in source_index
    assert "Start with [the import feature](_archive/epic-migration/children/feature-done.md)." in source_index
    assert "- [epic-migration](_archive/epic-migration/index.md)" in source_index
    assert "(epic-migration/" not in source_index
    assert source_index.count("# Items") == 1


def test_parent_archive_flattens_every_depth_under_one_archive_lane(tmp_path: Path) -> None:
    _release, epic, feature = _terminal_tree(tmp_path)
    nested = f"{feature}/children/bug-nested"
    write_item(tmp_path, nested, "type: Bug\nwork_status: resolved\n")
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), epic)

    assert plan.ok, plan.refusals
    assert plan.path_mapping[nested] == "work/_archive/epic-migration/children/feature-done/children/bug-nested"


def test_default_archive_targets_skip_active_archived_and_terminal_descendants() -> None:
    terminal = make_item("work/epic", type="Epic", work_status="resolved")
    child = make_item(
        "work/epic/children/feature",
        work_status="resolved",
        parent_path=terminal.path,
        ancestor_paths=(terminal.path,),
    )
    active = make_item("work/active", work_status="open")
    archived = make_item("work/_archive/done", work_status="resolved", archived=True)

    assert _default_targets((child, active, archived, terminal)) == (terminal.path,)


def test_archive_refuses_unknown_and_already_archived_targets(tmp_path: Path) -> None:
    archived_path = "work/_archive/bug-old"
    write_item(tmp_path, archived_path, "type: Bug\nwork_status: resolved\n")
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), ("work/missing", archived_path))

    assert {refusal.kind for refusal in plan.refusals} >= {"unknown-source", "already-archived"}


def test_archive_subtree_mapping_ignores_unknown_children() -> None:
    root = make_item("work/epic", child_paths=("work/missing",))
    assert _archive_subtree_mapping((root,), root.path, "work/_archive/epic") == {root.path: "work/_archive/epic"}
    assert _archive_subtree_mapping((root,), "work/missing", "work/_archive/missing") == {}


def test_archive_subtree_mapping_is_iterative_beyond_1080_edges() -> None:
    paths = ["work/epic-root"]
    for level in range(1_081):
        paths.append(f"{paths[-1]}/children/epic-{level}")
    items = tuple(
        make_item(
            path,
            type="Epic",
            work_status="resolved",
            parent_path=paths[index - 1] if index else None,
            child_paths=(paths[index + 1],) if index + 1 < len(paths) else (),
        )
        for index, path in enumerate(paths)
    )

    mapping = _archive_subtree_mapping(items, paths[0], "work/_archive/epic-root")

    assert len(mapping) == 1_082
    assert mapping[paths[-1]].count("/_archive/") == 1


def test_parent_archive_refuses_a_nonterminal_descendant_without_effects(tmp_path: Path) -> None:
    _release, epic, _feature = _terminal_tree(tmp_path, child_status="open")
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), (epic,))

    assert not plan.ok
    assert "nonterminal-descendant" in {refusal.kind for refusal in plan.refusals}
    assert plan.writes == plan.moves == plan.deletes == ()


def test_archive_requires_the_target_itself_to_be_terminal(tmp_path: Path) -> None:
    release, _epic, _feature = _terminal_tree(tmp_path)
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), (release,))

    assert not plan.ok
    assert "not-terminal" in {refusal.kind for refusal in plan.refusals}


def test_archive_refuses_an_existing_local_twin_without_writes(tmp_path: Path) -> None:
    feature = _terminal_leaf(tmp_path)
    destination = "work/_archive/bug-done"
    write_item(tmp_path, destination, "type: Feature\nwork_status: resolved\n")
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), (feature,))

    assert not plan.ok
    assert "dest-exists" in {refusal.kind for refusal in plan.refusals}
    assert plan.writes == plan.moves == plan.deletes == ()


def test_archive_plans_local_indexes_and_never_writes_during_planning(tmp_path: Path) -> None:
    feature = _terminal_leaf(tmp_path)
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), (feature,))

    members = {write.member for write in plan.writes}
    assert "work/index.md" in members
    assert "work/_archive/index.md" in members
    after = {path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


def test_archive_exposes_source_and_absent_destination_directory_preconditions(tmp_path: Path) -> None:
    _release, epic, _feature = _terminal_tree(tmp_path)
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), epic)

    conditions = {condition.member: condition.before_digest for condition in plan.directory_preconditions}
    assert conditions[epic] is not None
    assert conditions["work/_archive/epic-migration"] is None


def test_leaf_archive_preconditions_both_absent_conceptual_owned_roots(tmp_path: Path) -> None:
    feature = _terminal_leaf(tmp_path)
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), feature)

    destination = "work/_archive/bug-done"
    conditions = {condition.member: condition.before_digest for condition in plan.directory_preconditions}
    assert conditions[feature] is None
    assert conditions[destination] is None


def test_leaf_archive_apply_refuses_a_late_source_sidecar_without_effects(tmp_path: Path) -> None:
    feature = _terminal_leaf(tmp_path)
    bundle = load_bundle(tmp_path)
    plan = plan_archive(bundle, load_items(bundle), feature)
    late = tmp_path / feature / "references/late.bin"
    late.parent.mkdir(parents=True)
    late.write_bytes(b"appeared after planning\n")
    before_apply = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="changed since planning"):
        _apply_mutation(plan)

    assert _snapshot(tmp_path) == before_apply


def test_leaf_archive_refuses_a_preexisting_destination_owned_root_without_effects(tmp_path: Path) -> None:
    feature = _terminal_leaf(tmp_path)
    destination = tmp_path / "work/_archive/bug-done"
    destination.mkdir(parents=True)
    (destination / "unrelated.bin").write_bytes(b"do not adopt\n")
    before_plan = _snapshot(tmp_path)
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), feature)

    assert not plan.ok
    assert "directory-dest-exists" in {refusal.kind for refusal in plan.refusals}
    assert plan.moves == plan.writes == plan.deletes == plan.mkdirs == ()
    assert _snapshot(tmp_path) == before_plan


def test_leaf_archive_apply_refuses_a_post_plan_destination_owned_root_without_effects(tmp_path: Path) -> None:
    feature = _terminal_leaf(tmp_path)
    bundle = load_bundle(tmp_path)
    plan = plan_archive(bundle, load_items(bundle), feature)
    destination = tmp_path / "work/_archive/bug-done"
    destination.mkdir(parents=True)
    (destination / "unrelated.bin").write_bytes(b"do not adopt\n")
    before_apply = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="changed since planning"):
        _apply_mutation(plan)

    assert _snapshot(tmp_path) == before_apply


@pytest.mark.parametrize("destination_kind", ["directory", "symlink"])
def test_archive_apply_refuses_a_post_plan_destination_directory_without_effects(
    tmp_path: Path,
    destination_kind: str,
) -> None:
    _release, epic, _feature = _terminal_tree(tmp_path)
    bundle = load_bundle(tmp_path)
    plan = plan_archive(bundle, load_items(bundle), epic)
    destination = tmp_path / "work/_archive/epic-migration"
    destination.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-destination"
    outside.mkdir()
    (outside / "sentinel.txt").write_bytes(b"outside must stay unchanged\n")
    if destination_kind == "directory":
        destination.mkdir()
        (destination / "unrelated.txt").write_bytes(b"do not merge\n")
    else:
        destination.symlink_to(outside, target_is_directory=True)
    before_apply = _snapshot(tmp_path)
    outside_before = _snapshot(outside)

    with pytest.raises(ValueError, match=r"changed since planning|escapes the bundle root"):
        _apply_mutation(plan)

    assert _snapshot(tmp_path) == before_apply
    assert _snapshot(outside) == outside_before


def test_archive_apply_refuses_a_post_plan_destination_lane_directory_without_effects(tmp_path: Path) -> None:
    _release, epic, _feature = _terminal_tree(tmp_path)
    bundle = load_bundle(tmp_path)
    plan = plan_archive(bundle, load_items(bundle), epic)
    destination_lane = tmp_path / "work/_archive"
    destination_lane.mkdir()
    (destination_lane / "unrelated.txt").write_bytes(b"do not merge\n")
    before_apply = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="changed since planning"):
        _apply_mutation(plan)

    assert _snapshot(tmp_path) == before_apply


@pytest.mark.parametrize(
    "mutation",
    ["add", "remove", "change", "replace", "add-directory", "remove-directory"],
)
def test_archive_apply_refuses_a_changed_source_directory_manifest_without_effects(
    tmp_path: Path,
    mutation: str,
) -> None:
    release, epic, _feature = _terminal_tree(tmp_path)
    references = tmp_path / epic / "references"
    references.mkdir()
    tracked = references / "tracked.bin"
    removed_directory = references / "removed-directory"
    if mutation not in {"add", "add-directory", "remove-directory"}:
        tracked.write_bytes(b"planned bytes\n")
    if mutation == "remove-directory":
        removed_directory.mkdir()
    bundle = load_bundle(tmp_path)
    plan = plan_archive(bundle, load_items(bundle), epic)
    if mutation == "add":
        tracked.write_bytes(b"added after planning\n")
    elif mutation == "remove":
        tracked.unlink()
    elif mutation == "change":
        tracked.write_bytes(b"changed after planning\n")
    else:
        if mutation == "replace":
            tracked.unlink()
            tracked.symlink_to(tmp_path / f"{release}.md")
        elif mutation == "add-directory":
            (references / "added-directory/empty").mkdir(parents=True)
        else:
            removed_directory.rmdir()
    before_apply = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="changed since planning"):
        _apply_mutation(plan)

    assert _snapshot(tmp_path) == before_apply


def test_archive_apply_refuses_a_post_plan_write_preimage_symlink_escape(tmp_path: Path) -> None:
    _release, epic, _feature = _terminal_tree(tmp_path)
    owner = "work/bug-referrer"
    write_item(tmp_path, owner, "type: Bug\nwork_status: open\n")
    artifact = tmp_path / owner / "references/01-design.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(f"See [the epic](/{epic}.md).\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)
    plan = plan_archive(bundle, load_items(bundle), epic)
    assert artifact.relative_to(tmp_path).as_posix() in {write.member for write in plan.writes}

    outside = tmp_path.parent / f"{tmp_path.name}-outside-referrer"
    outside_artifact = outside / "references/01-design.md"
    outside_artifact.parent.mkdir(parents=True)
    outside_artifact.write_bytes(artifact.read_bytes())
    artifact.unlink()
    artifact.parent.rmdir()
    artifact.parent.parent.rmdir()
    artifact.parent.parent.symlink_to(outside, target_is_directory=True)
    before_apply = _snapshot(tmp_path)
    outside_before = _snapshot(outside)

    with pytest.raises(ValueError, match="escapes the bundle root"):
        _apply_mutation(plan)

    assert _snapshot(tmp_path) == before_apply
    assert _snapshot(outside) == outside_before


@pytest.mark.parametrize("mutation", ["change", "remove"])
def test_archive_apply_refuses_a_stale_moved_page_before_any_effect(tmp_path: Path, mutation: str) -> None:
    feature = _terminal_leaf(tmp_path)
    bundle = load_bundle(tmp_path)
    plan = plan_archive(bundle, load_items(bundle), feature)
    destination = plan.path_mapping[feature]
    write = next(write for write in plan.writes if write.member == f"{destination}.md")
    source = tmp_path / f"{feature}.md"
    assert write.source_member == f"{feature}.md"
    if mutation == "change":
        source.write_bytes(source.read_bytes() + b"\npost-plan change\n")
    else:
        source.unlink()
    before_apply = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="changed since planning"):
        _apply_mutation(plan)

    assert _snapshot(tmp_path) == before_apply


@pytest.mark.parametrize("mutation", ["change", "remove"])
def test_archive_apply_refuses_a_stale_moved_index_before_any_effect(tmp_path: Path, mutation: str) -> None:
    _release, epic, _feature = _terminal_tree(tmp_path)
    source_member = f"{epic}/children/index.md"
    source = tmp_path / source_member
    source.write_text("# Children\n\nHuman prose.\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)
    plan = plan_archive(bundle, load_items(bundle), epic)
    moved_write = next(write for write in plan.writes if write.source_member == source_member)
    assert moved_write.member == "work/_archive/epic-migration/children/index.md"
    if mutation == "change":
        source.write_text("post-plan change\n", encoding="utf-8")
    else:
        source.unlink()
    before_apply = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="changed since planning"):
        _apply_mutation(plan)

    assert _snapshot(tmp_path) == before_apply


def test_archiving_a_root_parent_reprojects_its_whole_subtree(tmp_path: Path) -> None:
    """The assertion the archive suite was missing: reload after applying.

    Every other test here checks the *plan*. A destination the path grammar
    cannot name still produces a well-formed plan, so planning-only assertions
    cannot see it -- the failure only appears when the moved tree is read back.
    """
    _release, epic, feature = _terminal_tree(tmp_path)
    nested = f"{feature}/children/bug-nested"
    write_item(tmp_path, nested, "type: Bug\nwork_status: resolved\n")
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), epic)
    assert plan.ok, plan.refusals
    _apply_mutation(plan)

    reprojected = {item.path: item for item in load_items(load_bundle(tmp_path, ignore=IGNORE))}
    assert "work/_archive/epic-migration" in reprojected
    moved_feature = "work/_archive/epic-migration/children/feature-done"
    moved_nested = f"{moved_feature}/children/bug-nested"
    assert moved_feature in reprojected
    assert moved_nested in reprojected

    assert reprojected[moved_nested].parent_path == moved_feature
    assert reprojected[moved_feature].parent_path == "work/_archive/epic-migration"
    assert all(reprojected[path].archived for path in ("work/_archive/epic-migration", moved_feature, moved_nested))
    assert not any(path.startswith("work/epic-migration") for path in reprojected)


def test_archive_moves_a_stray_item_level_index_instead_of_losing_it(tmp_path: Path) -> None:
    """A misplaced index.md living directly in an item's own directory (not
    a lane, not under references/) is still part of that item's owned
    subtree and must travel with it -- leaving it behind means the source
    directory never empties and the transactional apply aborts."""
    feature = _terminal_leaf(tmp_path)
    (tmp_path / feature).mkdir(parents=True, exist_ok=True)
    (tmp_path / feature / "index.md").write_text("stray\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), (feature,))
    assert plan.ok, plan.refusals

    stray_destination = "work/_archive/bug-done/index.md"
    moved_stray = any(move.dest == stray_destination for move in plan.moves)
    written_stray = any(write.member == stray_destination for write in plan.writes)
    assert moved_stray or written_stray, (plan.moves, plan.writes)

    _apply_mutation(plan)

    assert not (tmp_path / feature).exists()
    assert (tmp_path / stray_destination).read_text(encoding="utf-8") == "stray\n"


def test_archive_refuses_rather_than_crashes_on_an_unreadable_stray_item_index(tmp_path: Path) -> None:
    """A stray item-level index.md that failed to load (non-UTF-8 content) is
    never in `bundle.indexes`, so it can never be popped as a reserved alias.
    Before the fix, `_plan_path_mutation` still routed it into
    `reserved_item_indexes` and crashed with an uncaught `KeyError` instead of
    returning a graceful `MutationRefusal` -- violating the planner's
    write-free, refusal-as-data contract."""
    feature = _terminal_leaf(tmp_path)
    (tmp_path / feature).mkdir(parents=True, exist_ok=True)
    (tmp_path / feature / "index.md").write_bytes(b"\xffnot UTF-8\n")
    bundle = load_bundle(tmp_path)
    assert f"{feature}/index.md" in bundle.unreadable

    plan = plan_archive(bundle, load_items(bundle), (feature,))

    assert plan.ok is False
    assert any(refusal.path == f"{feature}/index.md" for refusal in plan.refusals), plan.refusals


def test_a_non_canonical_destination_is_refused_before_any_write(tmp_path: Path) -> None:
    """A mapping the grammar cannot name is a planning fault, so the plan must
    refuse rather than leave it to postcondition validation after the apply."""
    _release, epic, _feature = _terminal_tree(tmp_path)
    bundle = load_bundle(tmp_path)
    before = _snapshot(tmp_path)

    plan = _plan_path_mutation(
        bundle,
        load_items(bundle),
        "archive",
        {epic: "work/_archive/epic-migration/references/not-an-item"},
        roots=(epic,),
    )

    assert plan.ok is False
    non_canonical = [refusal for refusal in plan.refusals if refusal.kind == "non-canonical-destination"]
    assert [refusal.path for refusal in non_canonical] == [epic]
    assert _snapshot(tmp_path) == before


def test_default_targets_skip_a_terminal_child_under_a_live_ancestor() -> None:
    epic = make_item("work/epic-live", type="Epic", work_status="open")
    child = make_item(
        "work/epic-live/children/bug-done",
        work_status="resolved",
        parent_path=epic.path,
        ancestor_paths=(epic.path,),
    )

    assert _default_targets((epic, child)) == ()


def test_targeted_archive_of_a_child_under_a_live_ancestor_is_refused_as_not_top_level(tmp_path: Path) -> None:
    epic = "work/epic-live"
    child = f"{epic}/children/bug-done"
    write_item(tmp_path, epic, "type: Epic\nwork_status: open\n")
    write_item(tmp_path, child, "type: Bug\nwork_status: resolved\n")
    bundle = load_bundle(tmp_path)

    plan = plan_archive(bundle, load_items(bundle), (child,))

    assert not plan.ok
    assert "not-top-level" in {refusal.kind for refusal in plan.refusals}
    assert "archive work/epic-live" in " ".join(refusal.detail for refusal in plan.refusals)
    assert plan.writes == plan.moves == plan.deletes == ()


def test_archived_root_descendants_parse_as_archived(tmp_path: Path) -> None:
    from work_tracker_okf.paths import parse_item_path

    location = parse_item_path("work/_archive/epic-migration/children/feature-done")

    assert location is not None
    assert location.archived is True


def test_archive_succeeds_for_an_item_whose_stamped_design_artifact_was_never_written(
    tmp_path: Path,
) -> None:
    from okf_io import validate
    from work_tracker_okf.rules import lane_rules

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

    plan = plan_archive(bundle, load_items(bundle), ("work/bug-dangling",))
    assert plan.ok, plan.refusals

    _apply_mutation(plan)

    assert (tmp_path / "work/_archive/bug-dangling.md").exists()
    assert not (tmp_path / "work/bug-dangling.md").exists()
    moved_bundle = load_bundle(tmp_path, ignore=IGNORE)
    report = validate(moved_bundle, today=date(2026, 9, 2), extra_rules=lane_rules(repo_root=None))
    escapes = report.by_code("structure.source-escape")
    assert not escapes, escapes

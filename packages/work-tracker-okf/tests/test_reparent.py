import unicodedata
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote

import pytest
from okf_io import load_bundle
from work_helpers import make_item, write_item
from work_tracker_okf.cli import _apply_mutation
from work_tracker_okf.items import ARCHIVE_IGNORE, IGNORE, load_items
from work_tracker_okf.reparent import plan_release_adoption, plan_reparent


def _tree(root: Path) -> tuple[str, str, str]:
    release = "work/release-cutover"
    epic = "work/epic-migration"
    feature = f"{epic}/children/feature-import"
    write_item(root, release, "type: Release\nwork_status: open\n")
    write_item(root, epic, "type: Epic\nwork_status: open\n")
    write_item(root, feature, "type: Feature\nwork_status: open\n")
    (root / epic / "children").mkdir(parents=True, exist_ok=True)
    (root / release).mkdir(parents=True, exist_ok=True)
    return release, epic, feature


def test_reparent_planners_report_unknown_and_inactive_structural_inputs(tmp_path: Path) -> None:
    bundle = load_bundle(tmp_path)
    missing = plan_reparent(bundle, (), "work/missing", "work/parent")
    assert {refusal.kind for refusal in missing.refusals} == {"unknown-parent", "unknown-source"}

    inactive_parent = make_item("work/bug-parent", type="Bug", work_status="resolved")
    archived_source = make_item("work/_archive/feature", archived=True)
    refused = plan_reparent(
        bundle,
        (inactive_parent, archived_source),
        archived_source.path,
        inactive_parent.path,
    )
    assert {refusal.kind for refusal in refused.refusals} >= {
        "invalid-parent-type",
        "inactive-parent",
        "archived-source",
    }

    adoption_missing = plan_release_adoption(bundle, (), "work/source", "work/release")
    assert {refusal.kind for refusal in adoption_missing.refusals} == {"unknown-release", "unknown-source"}
    nested_release = make_item(
        "work/epic/children/release",
        type="Release",
        parent_path="work/epic",
        work_status="resolved",
    )
    terminal_source = make_item("work/source", work_status="resolved")
    adoption = plan_release_adoption(
        bundle,
        (nested_release, terminal_source),
        terminal_source.path,
        nested_release.path,
    )
    assert {refusal.kind for refusal in adoption.refusals} >= {
        "nested-release",
        "inactive-parent",
        "inactive-source",
    }


def test_reparent_maps_every_descendant_and_registered_reference(tmp_path: Path) -> None:
    release, epic, feature = _tree(tmp_path)
    referrer = "work/bug-referrer"
    write_item(
        tmp_path,
        referrer,
        "type: Bug\nwork_status: open\n"
        f"depends_on:\n  - path: {epic}\n    blocks: execute\n    needs: resolved\n"
        f"superseded_by: {feature}\n",
    )
    bundle = load_bundle(tmp_path)

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    destination = f"{release}/children/epic-migration"
    assert plan.ok, plan.refusals
    assert plan.path_mapping[epic] == destination
    assert plan.path_mapping[feature] == f"{destination}/children/feature-import"
    assert set(plan.validate_paths) == {
        destination,
        f"{destination}/children/feature-import",
        referrer,
    }
    rewritten = next(write.after for write in plan.writes if write.member == f"{referrer}.md").decode()
    assert f"path: {destination}" in rewritten
    assert f"superseded_by: {destination}/children/feature-import" in rewritten


def test_unchanged_lane_index_retains_digest_without_naming_itself_as_source(tmp_path: Path) -> None:
    release, epic, _feature = _tree(tmp_path)
    index = tmp_path / "work/index.md"
    index.write_text("# Work\n\nHuman prose.\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    write = next(write for write in plan.writes if write.member == "work/index.md")
    assert write.before_digest is not None
    assert write.source_member is None


def test_reparent_preserves_opaque_bytes_and_warns_only_for_old_paths(tmp_path: Path) -> None:
    release, epic, _feature = _tree(tmp_path)
    epic_page = tmp_path / f"{epic}.md"
    epic_page.write_text(
        epic_page.read_text(encoding="utf-8")
        + "\nSee [the opaque reserved artifact](epic-migration/references/index.md).\n",
        encoding="utf-8",
    )
    references = tmp_path / epic / "references"
    references.mkdir(parents=True)
    warning_bytes = f"private note about {epic}\n".encode()
    quiet_bytes = b"private note without a canonical path\n"
    binary_bytes = b"\xff\x00" + epic.encode()
    (references / "nested-note.md").write_bytes(warning_bytes)
    (references / "quiet.md").write_bytes(quiet_bytes)
    (references / "payload.md").write_bytes(binary_bytes)
    (references / "index.md").write_bytes(quiet_bytes)
    (references / "log.md").write_bytes(quiet_bytes)
    before = {path.name: path.read_bytes() for path in references.iterdir()}
    bundle = load_bundle(tmp_path, ignore=("*/references/*",))

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    assert plan.ok, plan.refusals
    opaque = {move.source: move for move in plan.moves if move.opaque}
    assert set(opaque) == {
        f"{epic}/references/nested-note.md",
        f"{epic}/references/payload.md",
        f"{epic}/references/quiet.md",
        f"{epic}/references/index.md",
        f"{epic}/references/log.md",
    }
    assert len(plan.warnings) == 1
    assert "nested-note.md" in plan.warnings[0]
    destination = f"{release}/children/epic-migration"
    moved_page = next(write.after.decode() for write in plan.writes if write.member == f"{destination}.md")
    assert "(epic-migration/references/index.md)" in moved_page
    assert {path.name: path.read_bytes() for path in references.iterdir()} == before


def test_registered_and_canonical_markdown_are_repaired_not_made_opaque(tmp_path: Path) -> None:
    release, epic, feature = _tree(tmp_path)
    page = tmp_path / f"{epic}.md"
    text = page.read_text(encoding="utf-8")
    page.write_text(
        text.replace(
            "work_status: open\n",
            f"work_status: open\nsources:\n  - id: design\n    resource: /{epic}/references/01-design.md\n",
        ),
        encoding="utf-8",
    )
    references = tmp_path / epic / "references"
    references.mkdir(parents=True)
    (references / "01-design.md").write_text(f"See [the feature](/{feature}.md).\n", encoding="utf-8")
    bundle = load_bundle(tmp_path, ignore=("*/references/*",))

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    assert plan.move_plan is not None
    design = next(move for move in plan.move_plan.moves if move.source.endswith("/references/01-design.md"))
    assert not design.opaque
    moved_design = next(write for write in plan.writes if write.member == design.dest).after.decode()
    assert f"/{release}/children/epic-migration/children/feature-import.md" in moved_design


@pytest.mark.parametrize("filename", ["index.md", "log.md"])
@pytest.mark.parametrize("ignore", [IGNORE, ARCHIVE_IGNORE], ids=["ignore", "archive-ignore"])
def test_registered_reserved_markdown_moves_with_structured_repair_under_every_lens(
    tmp_path: Path,
    filename: str,
    ignore: tuple[str, ...],
) -> None:
    release, epic, _feature = _tree(tmp_path)
    reserved = f"{epic}/references/{filename}"
    epic_page = tmp_path / f"{epic}.md"
    epic_page.write_text(
        epic_page.read_text(encoding="utf-8").replace(
            "work_status: open\n",
            f"work_status: open\nsources:\n  - id: reserved\n    resource: /{reserved}\n",
        ),
        encoding="utf-8",
    )
    owner = "work/bug-referrer"
    write_item(
        tmp_path,
        owner,
        "type: Bug\nwork_status: open\n",
        body=f"\nSee [the registered artifact](/{reserved}).\n",
    )
    artifact = tmp_path / reserved
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        "---\nresource: ../../bug-referrer.md\n---\nSee [the owner](../../bug-referrer.md).\n",
        encoding="utf-8",
    )
    bundle = load_bundle(tmp_path, ignore=ignore)

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    destination = f"{release}/children/epic-migration"
    destination_member = f"{destination}/references/{filename}"
    assert plan.ok, plan.refusals
    assert plan.move_plan is not None
    move = next(move for move in plan.move_plan.moves if move.source == reserved)
    assert move.dest == destination_member
    assert not move.opaque
    writes = {write.member: write.after.decode() for write in plan.writes}
    assert writes[destination_member].count("../../../../bug-referrer.md") == 2
    assert f"/{destination_member}" in writes[f"{owner}.md"]
    assert f"resource: /{destination_member}" in writes[f"{destination}.md"]


@pytest.mark.parametrize("ignore", [IGNORE, ARCHIVE_IGNORE], ids=["ignore", "archive-ignore"])
def test_registered_reserved_aliases_resolve_canonical_equivalent_paths(
    tmp_path: Path,
    ignore: tuple[str, ...],
) -> None:
    release, epic, _feature = _tree(tmp_path)
    nfd = unicodedata.normalize("NFD", "café")
    nfc = unicodedata.normalize("NFC", "café")
    reserved = f"{epic}/references/{nfd}/index.md"
    ordinary = f"{epic}/references/{nfd}/note.md"
    reserved_spelling = f"{epic}/references/{nfc}/index.md"
    ordinary_spelling = f"{epic}/references/{nfc}/note.md"
    epic_page = tmp_path / f"{epic}.md"
    epic_page.write_text(
        epic_page.read_text(encoding="utf-8").replace(
            "work_status: open\n",
            "work_status: open\n"
            f"sources:\n  - id: reserved\n    resource: /{reserved_spelling}\n"
            f"  - id: ordinary\n    resource: /{ordinary_spelling}\n",
        ),
        encoding="utf-8",
    )
    references = tmp_path / epic / "references" / nfd
    references.mkdir(parents=True)
    (tmp_path / reserved).write_text(f"See [ordinary](/{ordinary_spelling}).\n", encoding="utf-8")
    (tmp_path / ordinary).write_text(f"See [reserved](/{reserved_spelling}).\n", encoding="utf-8")
    external = "work/bug-referrer"
    write_item(
        tmp_path,
        external,
        "type: Bug\nwork_status: open\n",
        body=f"\nSee [reserved](/{reserved_spelling}) and [ordinary](/{ordinary_spelling}).\n",
    )
    bundle = load_bundle(tmp_path, ignore=ignore)

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    destination = f"{release}/children/epic-migration"
    reserved_destination = f"{destination}/references/{nfd}/index.md"
    ordinary_destination = f"{destination}/references/{nfd}/note.md"
    reserved_link = quote(f"/{reserved_destination}", safe="/")
    ordinary_link = quote(f"/{ordinary_destination}", safe="/")
    assert plan.ok, plan.refusals
    assert plan.move_plan is not None
    moves = {move.source: move for move in plan.move_plan.moves}
    assert moves[reserved].dest == reserved_destination
    assert moves[ordinary].dest == ordinary_destination
    assert not moves[reserved].opaque
    assert not moves[ordinary].opaque
    writes = {write.member: write for write in plan.writes}
    assert ordinary_link in writes[reserved_destination].after.decode()
    assert reserved_link in writes[ordinary_destination].after.decode()
    assert reserved_link in writes[f"{external}.md"].after.decode()
    assert ordinary_link in writes[f"{external}.md"].after.decode()
    assert f"resource: /{reserved_destination}" in writes[f"{destination}.md"].after.decode()
    assert f"resource: /{ordinary_destination}" in writes[f"{destination}.md"].after.decode()
    assert writes[reserved_destination].source_member == reserved
    assert writes[ordinary_destination].source_member == ordinary
    assert writes[reserved_destination].before_digest is not None
    assert writes[ordinary_destination].before_digest is not None
    assert {reserved, ordinary, f"{external}.md"} <= set(plan.move_plan.digests)
    assert set(plan.validate_paths) == {destination, f"{destination}/children/feature-import", external}
    assert all(".work-tracker-reserved-" not in move.source for move in plan.move_plan.moves)
    assert all(".work-tracker-reserved-" not in move.dest for move in plan.move_plan.moves)
    assert all(".work-tracker-reserved-" not in member for member in writes)
    assert all(".work-tracker-reserved-" not in write.after.decode() for write in plan.writes)
    assert all(".work-tracker-reserved-" not in member for member in plan.move_plan.digests)


def test_external_registered_and_canonical_artifacts_are_repaired_under_ignore(tmp_path: Path) -> None:
    release, epic, _feature = _tree(tmp_path)
    owner = "work/bug-referrer"
    registered = f"{owner}/references/context.md"
    canonical = f"{owner}/references/01-design.md"
    write_item(
        tmp_path,
        owner,
        f"type: Bug\nwork_status: open\nsources:\n  - id: context\n    resource: /{registered}\n",
    )
    references = tmp_path / owner / "references"
    references.mkdir(parents=True)
    for member in (registered, canonical):
        (tmp_path / member).write_text(f"See [the epic](/{epic}.md).\n", encoding="utf-8")
    bundle = load_bundle(tmp_path, ignore=IGNORE)

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    assert plan.ok, plan.refusals
    by_member = {write.member: write.after.decode() for write in plan.writes}
    assert f"/{release}/children/epic-migration.md" in by_member[registered]
    assert f"/{release}/children/epic-migration.md" in by_member[canonical]
    assert owner in plan.validate_paths


def test_external_unregistered_artifact_stays_opaque_under_archive_ignore(tmp_path: Path) -> None:
    release, epic, _feature = _tree(tmp_path)
    owner = "work/bug-referrer"
    write_item(tmp_path, owner, "type: Bug\nwork_status: open\n")
    member = f"{owner}/references/scratch.md"
    artifact = tmp_path / member
    artifact.parent.mkdir(parents=True)
    before = f"Private [old link](/{epic}.md).\n".encode()
    artifact.write_bytes(before)
    bundle = load_bundle(tmp_path, ignore=ARCHIVE_IGNORE)

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    assert plan.ok, plan.refusals
    assert member not in {write.member for write in plan.writes}
    assert plan.move_plan is not None
    assert member not in {edit.member for edit in plan.move_plan.edits}
    assert plan.warnings == (
        f"{member}: opaque UTF-8 content mentions old canonical path {epic!r}; bytes remain unchanged",
    )
    assert artifact.read_bytes() == before


def test_reparent_refuses_descendant_parent_nested_release_and_leaf_parent(tmp_path: Path) -> None:
    release, epic, feature = _tree(tmp_path)
    leaf = "work/bug-leaf"
    write_item(tmp_path, leaf, "type: Bug\nwork_status: open\n")
    bundle = load_bundle(tmp_path)
    items = load_items(bundle)

    cycle = plan_reparent(bundle, items, epic, feature)
    nested_release = plan_reparent(bundle, items, release, leaf)

    assert not cycle.ok
    assert "descendant-destination" in {refusal.kind for refusal in cycle.refusals}
    assert not nested_release.ok
    assert {"nested-release", "invalid-parent-type"} <= {refusal.kind for refusal in nested_release.refusals}
    assert nested_release.writes == nested_release.moves == nested_release.deletes == ()


def test_reparent_refuses_a_nested_release_destination_parent(tmp_path: Path) -> None:
    _release, epic, _feature = _tree(tmp_path)
    nested_release = f"{epic}/children/release-invalid"
    source = "work/bug-source"
    write_item(tmp_path, nested_release, "type: Release\nwork_status: open\n")
    write_item(tmp_path, source, "type: Bug\nwork_status: open\n")
    bundle = load_bundle(tmp_path)

    plan = plan_reparent(bundle, load_items(bundle), source, nested_release)

    assert not plan.ok
    assert "nested-release" in {refusal.kind for refusal in plan.refusals}
    assert plan.writes == plan.moves == plan.deletes == ()


def test_reparent_refuses_destination_collisions_without_writes(tmp_path: Path) -> None:
    release, epic, _feature = _tree(tmp_path)
    collision = f"{release}/children/epic-migration"
    write_item(tmp_path, collision, "type: Epic\nwork_status: open\n")
    bundle = load_bundle(tmp_path)

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    assert not plan.ok
    assert "dest-exists" in {refusal.kind for refusal in plan.refusals}
    assert plan.writes == plan.moves == plan.deletes == ()


def test_reparent_refuses_an_existing_owned_destination_directory(tmp_path: Path) -> None:
    release, epic, _feature = _tree(tmp_path)
    destination = tmp_path / release / "children" / "epic-migration"
    destination.mkdir(parents=True)
    (destination / "unrelated.txt").write_text("do not merge\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    assert not plan.ok
    assert "directory-dest-exists" in {refusal.kind for refusal in plan.refusals}
    assert plan.writes == plan.moves == plan.deletes == ()


def test_reparent_preserves_empty_nested_directories_and_cleans_the_source(tmp_path: Path) -> None:
    release, epic, _feature = _tree(tmp_path)
    empty = f"{epic}/references/empty/deeper"
    (tmp_path / empty).mkdir(parents=True)
    bundle = load_bundle(tmp_path)

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    destination = f"{release}/children/epic-migration"
    assert plan.ok, plan.refusals
    assert f"{destination}/references/empty/deeper" in plan.mkdirs
    assert empty in plan.deletes

    _apply_mutation(plan)

    assert (tmp_path / destination / "references/empty/deeper").is_dir()
    assert not (tmp_path / epic).exists()


def test_reparent_preconditions_an_absent_implicit_mkdir_ancestor(tmp_path: Path) -> None:
    release, epic, _feature = _tree(tmp_path)
    bundle = load_bundle(tmp_path)
    plan = plan_reparent(bundle, load_items(bundle), epic, release)
    conditions = {condition.member: condition.before_digest for condition in plan.directory_preconditions}
    nested_ancestor = f"{release}/children"
    assert conditions[nested_ancestor] is None
    ancestor = tmp_path / nested_ancestor
    ancestor.mkdir()
    (ancestor / "unrelated.bin").write_bytes(b"do not merge\n")
    before_apply = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }

    with pytest.raises(ValueError, match="changed since planning"):
        _apply_mutation(plan)

    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    } == before_apply


def test_reparent_refuses_registered_malformed_markdown_but_moves_unregistered_malformed_opaque(
    tmp_path: Path,
) -> None:
    release, epic, _feature = _tree(tmp_path)
    page = tmp_path / f"{epic}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "work_status: open\n",
            f"work_status: open\nsources:\n  - id: design\n    resource: /{epic}/references/01-design.md\n",
        ),
        encoding="utf-8",
    )
    references = tmp_path / epic / "references"
    references.mkdir(parents=True)
    (references / "01-design.md").write_text("---\nunterminated: [", encoding="utf-8")
    (references / "scratch.md").write_text("---\nunterminated: [", encoding="utf-8")
    bundle = load_bundle(tmp_path, ignore=("*/references/*",))

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    assert not plan.ok
    assert "parse-error" in {refusal.kind for refusal in plan.refusals}
    assert plan.writes == plan.moves == plan.deletes == ()
    assert plan.move_plan is not None
    assert any(move.source.endswith("scratch.md") and move.opaque for move in plan.move_plan.moves)


@pytest.mark.parametrize("filename", ["index.md", "log.md"])
@pytest.mark.parametrize("ignore", [IGNORE, ARCHIVE_IGNORE], ids=["ignore", "archive-ignore"])
def test_reparent_refuses_invalid_utf8_registered_reserved_markdown_without_effects(
    tmp_path: Path,
    filename: str,
    ignore: tuple[str, ...],
) -> None:
    release, epic, _feature = _tree(tmp_path)
    member = f"{epic}/references/{filename}"
    epic_page = tmp_path / f"{epic}.md"
    epic_page.write_text(
        epic_page.read_text(encoding="utf-8").replace(
            "work_status: open\n",
            f"work_status: open\nsources:\n  - id: malformed\n    resource: /{member}\n",
        ),
        encoding="utf-8",
    )
    artifact = tmp_path / member
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"\xffnot UTF-8\n")
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    bundle = load_bundle(tmp_path, ignore=ignore)

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    assert not plan.ok
    assert "parse-error" in {refusal.kind for refusal in plan.refusals}
    assert plan.writes == plan.moves == plan.deletes == plan.mkdirs == ()
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    } == before


@pytest.mark.parametrize("filename", ["index.md", "log.md"])
@pytest.mark.parametrize("ambiguous", [False, True], ids=["single", "ambiguous"])
def test_reparent_registers_every_canonical_equivalent_unreadable_reserved_member(
    tmp_path: Path,
    filename: str,
    *,
    ambiguous: bool,
) -> None:
    release, epic, _feature = _tree(tmp_path)
    nfd = unicodedata.normalize("NFD", "café")
    nfc = unicodedata.normalize("NFC", "café")
    raw_member = f"{epic}/references/{nfd}/{filename}"
    cited_member = f"{epic}/references/{nfc}/{filename}"
    epic_page = tmp_path / f"{epic}.md"
    epic_page.write_text(
        epic_page.read_text(encoding="utf-8").replace(
            "work_status: open\n",
            f"work_status: open\nsources:\n  - id: unreadable\n    resource: /{cited_member}\n",
        ),
        encoding="utf-8",
    )
    artifact = tmp_path / raw_member
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"\xffnot UTF-8\n")
    scratch = f"{epic}/references/scratch.md"
    (tmp_path / scratch).write_text(f"Unregistered note about {epic}.\n", encoding="utf-8")
    loaded = load_bundle(tmp_path, ignore=ARCHIVE_IGNORE)
    unreadable = dict(loaded.unreadable)
    assert raw_member in unreadable
    raw_candidates = {raw_member}
    if ambiguous:
        unreadable[cited_member] = "synthetic canonical-equivalent unreadable candidate"
        raw_candidates.add(cited_member)
    bundle = replace(
        loaded,
        unreadable=MappingProxyType(unreadable),
        _canonical=MappingProxyType({}),
    )
    assert bundle.member_id(cited_member) is None

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    assert not plan.ok
    parse_refusals = {refusal.path for refusal in plan.refusals if refusal.kind == "parse-error"}
    assert raw_candidates <= parse_refusals
    assert plan.writes == plan.moves == plan.deletes == plan.mkdirs == ()
    assert plan.warnings == (
        f"{scratch}: opaque UTF-8 content mentions old canonical path {epic!r}; bytes remain unchanged",
    )


def test_release_adoption_moves_one_root_subtree_and_keeps_release_at_root(tmp_path: Path) -> None:
    release, epic, feature = _tree(tmp_path)
    bundle = load_bundle(tmp_path)

    plan = plan_release_adoption(bundle, load_items(bundle), epic, release)

    assert plan.ok, plan.refusals
    assert release not in plan.path_mapping
    assert plan.path_mapping == {
        epic: f"{release}/children/epic-migration",
        feature: f"{release}/children/epic-migration/children/feature-import",
    }


def test_release_adoption_refuses_non_release_and_non_root_sources(tmp_path: Path) -> None:
    release, epic, feature = _tree(tmp_path)
    bundle = load_bundle(tmp_path)
    items = load_items(bundle)

    wrong_parent = plan_release_adoption(bundle, items, epic, epic)
    release_source = plan_release_adoption(bundle, items, release, release)
    nested_source = plan_release_adoption(bundle, items, feature, release)

    assert "not-a-release" in {refusal.kind for refusal in wrong_parent.refusals}
    assert "nested-release" in {refusal.kind for refusal in release_source.refusals}
    assert "not-a-root" in {refusal.kind for refusal in nested_source.refusals}


def test_reparent_merges_source_destination_and_owned_lane_indexes(tmp_path: Path) -> None:
    release, epic, feature = _tree(tmp_path)
    old_index = tmp_path / epic / "children" / "index.md"
    old_index.write_text(f"# Children\n\nSee [feature](/{feature}.md).\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    destination = f"{release}/children/epic-migration"
    by_member = {write.member: write for write in plan.writes}
    assert "work/index.md" in by_member
    assert f"{release}/children/index.md" in by_member
    assert f"{destination}/children/index.md" in by_member
    moved_index = by_member[f"{destination}/children/index.md"].after
    assert b"See [feature]" in moved_index
    assert f"/{destination}/children/feature-import.md".encode() in moved_index
    assert f"{epic}/children/index.md" in plan.deletes


def test_symlink_escape_refuses_the_whole_subtree(tmp_path: Path) -> None:
    release, epic, _feature = _tree(tmp_path)
    outside = tmp_path.parent / "outside-note.md"
    outside.write_text("outside\n", encoding="utf-8")
    references = tmp_path / epic / "references"
    references.mkdir(parents=True)
    (references / "outside.md").symlink_to(outside)
    bundle = load_bundle(tmp_path)

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    assert not plan.ok
    assert "symlink-escape" in {refusal.kind for refusal in plan.refusals}
    assert plan.writes == plan.moves == plan.deletes == ()


def test_reparent_moves_the_execute_coverage_artifact(tmp_path: Path) -> None:
    # The point is that `_CANONICAL_FILENAMES` needs no per-file branch: a new
    # managed artifact must move -- and have its links repaired -- with zero new
    # code. An artifact absent from that frozenset is a file that silently stays
    # behind on a reparent.
    release, epic, feature = _tree(tmp_path)
    references = tmp_path / epic / "references"
    references.mkdir(parents=True)
    (references / "03-execute-coverage.md").write_text(
        f"- [x] one -- see [the feature](/{feature}.md).\n", encoding="utf-8"
    )
    bundle = load_bundle(tmp_path, ignore=("*/references/*",))

    plan = plan_reparent(bundle, load_items(bundle), epic, release)

    assert plan.move_plan is not None
    move = next(
        candidate
        for candidate in plan.move_plan.moves
        if candidate.source.endswith("/references/03-execute-coverage.md")
    )
    assert not move.opaque
    assert move.dest == f"{release}/children/epic-migration/references/03-execute-coverage.md"
    moved = next(write for write in plan.writes if write.member == move.dest).after.decode()
    assert f"/{release}/children/epic-migration/children/feature-import.md" in moved

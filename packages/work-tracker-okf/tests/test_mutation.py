from pathlib import Path
from types import MappingProxyType

import pytest
from okf_io import load_bundle
from work_helpers import make_item
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
        active_child_paths=("work/feature", "work/missing"),
    )
    child = make_item(
        "work/feature",
        parent_path=root.path,
        ancestor_paths=(root.path,),
        archived_child_paths=(root.path,),
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
        active_child_paths=("work/feature",),
        archived_child_paths=("work/_archive/bug",),
    )
    child = make_item("work/feature", type="Bug", parent_path=root.path, ancestor_paths=(root.path,))
    mapping = {root.path: "work/_archive/epic", child.path: "work/_archive/epic/children/_archive/feature"}
    future = _future_items((root, child), mapping)
    assert future[0].archived
    assert future[1].parent_path == "work/_archive/epic"
    assert _lane_mapping((root, child), mapping) == {
        "work/epic/children": "work/_archive/epic/children",
        "work/epic/children/_archive": "work/_archive/epic/children/_archive",
    }
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

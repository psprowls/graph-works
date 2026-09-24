from pathlib import Path

import pytest
from work_tracker_okf import paths
from work_tracker_okf.paths import PHASE_ORDINALS, ArtifactRef, checkpoint_ref


def test_nested_archive_path_derives_owner_and_ancestors() -> None:
    location = paths.parse_item_path("work/release-cutover/children/epic-migration/children/_archive/bug-old-link")
    assert location is not None
    assert location.parent_path == "work/release-cutover/children/epic-migration"
    assert location.ancestor_paths == (
        "work/release-cutover",
        "work/release-cutover/children/epic-migration",
    )
    assert location.lane == "work/release-cutover/children/epic-migration/children/_archive"
    assert location.archived is True
    assert location.page == "work/release-cutover/children/epic-migration/children/_archive/bug-old-link.md"


@pytest.mark.parametrize(
    ("item_path", "lane", "archived"),
    [
        ("work/release-cutover", "work", False),
        ("work/_archive/release-cutover", "work/_archive", True),
    ],
)
def test_root_item_paths_parse_in_active_and_archive_lanes(item_path: str, lane: str, archived: bool) -> None:
    location = paths.parse_item_path(item_path)
    assert location is not None
    assert location.path == item_path
    assert location.lane == lane
    assert location.parent_path is None
    assert location.ancestor_paths == ()
    assert location.archived is archived


@pytest.mark.parametrize(
    "member",
    [
        "index",
        "work/index",
        "work/release-cutover.md",
        "work/release-cutover/references",
        "work/release-cutover/children",
        "work/release-cutover/children/_archive",
        "work/release-cutover/children/epic/bug",
        "work/release-cutover/children/_archive/bug/children",
        "work/_archive/_archive/release-cutover",
        "work/release-cutover/children/children/epic-migration",
    ],
)
def test_parser_refuses_non_item_members(member: str) -> None:
    assert paths.parse_item_path(member) is None


@pytest.mark.parametrize(
    ("item_path", "parent_path"),
    [
        ("work/_archive/release-cutover/children/epic-migration", "work/_archive/release-cutover"),
        ("work/_archive/release-cutover/children/_archive/epic-migration", "work/_archive/release-cutover"),
        (
            "work/release-cutover/children/_archive/epic-migration/children/_archive/feature-import",
            "work/release-cutover/children/_archive/epic-migration",
        ),
    ],
)
def test_an_archive_lane_may_hold_a_subtree_at_any_depth(item_path: str, parent_path: str) -> None:
    """Archiving moves a family intact, so `_archive` is a lane the walk passes
    through rather than one that ends the path."""
    location = paths.parse_item_path(item_path)
    assert location is not None
    assert location.path == item_path
    assert location.parent_path == parent_path
    assert location.archived is True


def test_archived_state_is_inherited_by_every_descendant_of_an_archive_lane() -> None:
    """An item in an *active* `children/` lane below an archive lane is still
    archived: the nearest archive ancestor decides, not the item's own lane."""
    location = paths.parse_item_path("work/_archive/release-cutover/children/epic-migration")
    assert location is not None
    assert location.lane == "work/_archive/release-cutover/children"
    assert location.archived is True


def test_path_helpers_preserve_the_extensionless_identity() -> None:
    item_path = "work/release-cutover/children/epic-migration"
    assert paths.item_page(item_path) == ArtifactRef(rel=f"{item_path}.md")
    assert paths.owned_dir(item_path) == ArtifactRef(rel=item_path)
    assert paths.references_dir(item_path) == ArtifactRef(rel=f"{item_path}/references")
    assert paths.child_lane(item_path) == f"{item_path}/children"
    assert paths.child_lane(item_path, archived=True) == f"{item_path}/children/_archive"


def test_managed_artifacts_have_root_absolute_resource_and_filename_ids(tmp_path: Path) -> None:
    item_path = "work/release-cutover"
    ref = paths.artifact_ref(item_path, paths.MANAGED_ARTIFACTS["execute-results"])
    assert ref == ArtifactRef(rel="work/release-cutover/references/03-execute-results.md", source_id="execute-results")
    assert ref.resource == "/work/release-cutover/references/03-execute-results.md"
    assert ref.path(tmp_path) == tmp_path / ref.rel
    assert paths.source_id_for_filename("03-execute-results.md") == "execute-results"


def test_the_execute_coverage_artifact_derives_its_id_from_its_filename(tmp_path: Path) -> None:
    item_path = "work/release-cutover"
    ref = paths.artifact_ref(item_path, paths.MANAGED_ARTIFACTS["execute-coverage"])
    assert paths.MANAGED_ARTIFACTS["execute-coverage"] == "03-execute-coverage.md"
    assert ref == ArtifactRef(
        rel="work/release-cutover/references/03-execute-coverage.md",
        source_id="execute-coverage",
    )
    assert ref.resource == "/work/release-cutover/references/03-execute-coverage.md"
    assert ref.path(tmp_path) == tmp_path / ref.rel
    assert paths.source_id_for_filename("03-execute-coverage.md") == "execute-coverage"


def test_the_coverage_artifact_is_a_canonical_member_that_moves() -> None:
    # `_CANONICAL_FILENAMES` is what makes reparent/archive relocate the file
    # without a per-filename branch; a managed artifact absent from it is a
    # file that silently stays behind.
    from work_tracker_okf.mutation import _CANONICAL_FILENAMES

    assert "03-execute-coverage.md" in _CANONICAL_FILENAMES


def test_checkpoint_ref_uses_the_phase_ordinal_and_decision_id() -> None:
    ref = checkpoint_ref("work/epic-a/children/feature-b", "execute", "D-007")
    assert ref.rel == "work/epic-a/children/feature-b/references/03-execute-checkpoint-D-007.md"
    assert ref.resource == "/work/epic-a/children/feature-b/references/03-execute-checkpoint-D-007.md"
    assert ref.source_id is None
    assert dict(PHASE_ORDINALS) == {"design": "01", "plan": "02", "execute": "03", "finish": "04"}


@pytest.mark.parametrize(
    ("phase", "decision_id"),
    [("entry", "D-001"), ("done", "D-001"), ("plan", "pending")],
)
def test_checkpoint_ref_rejects_unparkable_phases_and_ids(phase: str, decision_id: str) -> None:
    with pytest.raises(ValueError):
        checkpoint_ref("work/a", phase, decision_id)


@pytest.mark.parametrize(
    ("phase", "filename"),
    [
        ("design", "01-design-transcript.jsonl"),
        ("plan", "02-plan-transcript.jsonl"),
        ("execute", "03-execute-transcript.jsonl"),
        ("finish", "04-finish-transcript.jsonl"),
    ],
)
def test_every_phase_has_a_transcript_managed_artifact(phase: str, filename: str, tmp_path: Path) -> None:
    key = f"{phase}-transcript"
    assert paths.MANAGED_ARTIFACTS[key] == filename
    ref = paths.artifact_ref("work/release-cutover", paths.MANAGED_ARTIFACTS[key])
    assert ref == ArtifactRef(rel=f"work/release-cutover/references/{filename}", source_id=key)
    assert ref.resource == f"/work/release-cutover/references/{filename}"


def test_finish_receipt_is_separate_managed_artifact():
    from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref
    from work_tracker_okf.vocabulary import FINISH_RECEIPT_SOURCE_ID

    ref = artifact_ref("work/epic-a", MANAGED_ARTIFACTS[FINISH_RECEIPT_SOURCE_ID])
    assert ref.source_id == "finish-receipt"
    assert ref.rel == "work/epic-a/references/04-finish-receipt.md"

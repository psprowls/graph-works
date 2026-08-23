from pathlib import Path

import pytest
from work_tracker_okf import paths
from work_tracker_okf.paths import ArtifactRef


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

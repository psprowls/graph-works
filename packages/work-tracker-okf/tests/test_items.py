from okf_io import Bundle
from work_helpers import load_written_items, write_item
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.items import WorkItem, item_index, load_items


def test_projection_derives_containment_and_archive_children(path_native_bundle: Bundle) -> None:
    epic = item_index(load_items(path_native_bundle))["work/release-cutover/children/epic-migration"]
    assert epic.parent_path == "work/release-cutover"
    assert epic.ancestor_paths == ("work/release-cutover",)
    assert epic.active_child_paths == ("work/release-cutover/children/epic-migration/children/feature-import",)
    assert epic.archived_child_paths == ("work/release-cutover/children/epic-migration/children/_archive/bug-old-link",)


def test_projection_uses_path_not_frontmatter_for_permanent_containment(tmp_path) -> None:
    write_item(
        tmp_path, "work/release/children/epic", "type: Epic\nwork_status: open\nparent: made-up\nchildren: [made-up]\n"
    )
    item = load_written_items(tmp_path)[0]
    assert item.parent_path == "work/release"
    assert item.active_child_paths == ()


def test_projection_uses_extensionless_path_as_key_and_page_as_location(path_native_bundle: Bundle) -> None:
    release = item_index(load_items(path_native_bundle))["work/release-cutover"]
    assert release.path == "work/release-cutover"
    assert release.page_path == "work/release-cutover.md"
    assert release.basename == "release-cutover"
    assert release.work_status == "open"


def test_malformed_content_projects_empty_values_and_dependency_issues(tmp_path) -> None:
    write_item(tmp_path, "work/broken", "type: 7\nwork_status: []\nopened: {}\ndepends_on: [legacy, {slug: old}]\n")
    item = load_written_items(tmp_path)[0]
    assert item.type == ""
    assert item.work_status == ""
    assert item.opened == ""
    assert item.dependency_edges == ()
    assert {issue.code for issue in item.dependency_issues} == {"invalid-entry", "unknown-keys", "missing-keys"}


def test_structured_dependencies_project_with_full_paths(tmp_path) -> None:
    write_item(
        tmp_path,
        "work/feature",
        "type: Feature\nwork_status: open\ndepends_on:\n"
        "  - path: work/release/children/epic\n    blocks: plan\n    needs: design\n",
    )
    assert load_written_items(tmp_path)[0].dependency_edges == (
        DependencyEdge("work/release/children/epic", "plan", "design"),
    )


def test_items_are_immutable_and_sorted_by_full_path(path_native_bundle: Bundle) -> None:
    items = load_items(path_native_bundle)
    assert [item.path for item in items] == sorted(item.path for item in items)
    assert WorkItem.__dataclass_params__.frozen is True

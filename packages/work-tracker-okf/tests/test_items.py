from dataclasses import replace

from okf_io import Bundle
from work_helpers import load_written_items, write_item
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.items import Stamp, WorkItem, item_index, load_items, unreadable_detail


def test_projection_derives_containment_and_every_direct_child(path_native_bundle: Bundle) -> None:
    epic = item_index(load_items(path_native_bundle))["work/release-cutover/children/epic-migration"]
    assert epic.parent_path == "work/release-cutover"
    assert epic.ancestor_paths == ("work/release-cutover",)
    assert epic.child_paths == (
        "work/release-cutover/children/epic-migration/children/bug-old-link",
        "work/release-cutover/children/epic-migration/children/feature-import",
    )


def test_projection_uses_path_not_frontmatter_for_permanent_containment(tmp_path) -> None:
    write_item(
        tmp_path, "work/release/children/epic", "type: Epic\nwork_status: open\nparent: made-up\nchildren: [made-up]\n"
    )
    item = load_written_items(tmp_path)[0]
    assert item.parent_path == "work/release"
    assert item.child_paths == ()


def test_unreadable_detail_looks_up_by_the_dotmd_path(path_native_bundle: Bundle) -> None:
    bundle = replace(
        path_native_bundle, unreadable={"work/locked.md": "could not be read: [Errno 13] Permission denied"}
    )
    assert unreadable_detail(bundle, "work/locked") == "could not be read: [Errno 13] Permission denied"
    assert unreadable_detail(bundle, "work/not-locked") is None


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


_BASE = "type: Feature\nwork_status: open\nopened: 2026-09-01\nupdated: 2026-09-01\n"


def test_repo_and_repo_stamps_project(tmp_path) -> None:
    write_item(
        tmp_path,
        "work/feature-a",
        _BASE + "repo: code\nrepo_stamps:\n  ui:\n    worktree: /wt/ui\n    branch: epic/x\n",
    )
    (item,) = load_written_items(tmp_path)
    assert item.repo == "code"
    assert dict(item.repo_stamps) == {"ui": Stamp("/wt/ui", "epic/x")}
    assert item.invalid_optional_fields == ()


def test_absent_repo_fields_project_empty(tmp_path) -> None:
    write_item(tmp_path, "work/feature-a", _BASE)
    (item,) = load_written_items(tmp_path)
    assert item.repo is None
    assert dict(item.repo_stamps) == {}
    assert item.invalid_optional_fields == ()


def test_malformed_repo_fields_project_as_absent_and_are_named(tmp_path) -> None:
    write_item(
        tmp_path,
        "work/feature-a",
        _BASE + "repo: 3\nrepo_stamps:\n  ui:\n    worktree: /wt/ui\n  ok:\n    worktree: /wt/ok\n    branch: b\n",
    )
    (item,) = load_written_items(tmp_path)
    assert item.repo is None
    assert dict(item.repo_stamps) == {"ok": Stamp("/wt/ok", "b")}
    assert item.invalid_optional_fields == ("repo", "repo_stamps")


def test_a_non_mapping_repo_stamps_is_a_projection_problem(tmp_path) -> None:
    write_item(tmp_path, "work/feature-a", _BASE + "repo_stamps: [ui]\n")
    (item,) = load_written_items(tmp_path)
    assert dict(item.repo_stamps) == {}
    assert item.invalid_optional_fields == ("repo_stamps",)

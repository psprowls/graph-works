from dataclasses import replace

import pytest
from okf_io import Bundle
from work_helpers import load_written_items, write_item
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.items import (
    SpecBaseline,
    Stamp,
    WorkItem,
    is_commit_oid,
    item_index,
    load_items,
    spec_baseline_of,
    unreadable_detail,
)
from work_tracker_okf.obligations import Obligation


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


def test_finish_obligations_project_in_order(tmp_path) -> None:
    write_item(
        tmp_path,
        "work/feature-a",
        _BASE + "finish_obligations:\n"
        "  - text: Tag the release\n    origin: deferred\n    recorded: 2026-09-20\n"
        "  - text: Check coverage\n    origin: coverage\n    recorded: 2026-09-21\n",
    )
    (item,) = load_written_items(tmp_path)
    assert item.finish_obligations == (
        Obligation("Tag the release", "deferred", "2026-09-20"),
        Obligation("Check coverage", "coverage", "2026-09-21"),
    )
    assert "finish_obligations" not in item.invalid_optional_fields


def test_malformed_finish_obligations_keep_good_entries_and_are_named(tmp_path) -> None:
    write_item(
        tmp_path,
        "work/feature-a",
        _BASE + "finish_obligations:\n"
        "  - text: Tag the release\n    origin: deferred\n    recorded: 2026-09-20\n"
        "  - text: bad\n    origin: someday\n    recorded: 2026-09-20\n",
    )
    (item,) = load_written_items(tmp_path)
    assert item.finish_obligations == (Obligation("Tag the release", "deferred", "2026-09-20"),)
    assert "finish_obligations" in item.invalid_optional_fields


def test_a_mapping_finish_obligations_projects_empty_and_is_named(tmp_path) -> None:
    write_item(tmp_path, "work/feature-a", _BASE + "finish_obligations:\n  text: x\n")
    (item,) = load_written_items(tmp_path)
    assert item.finish_obligations == ()
    assert "finish_obligations" in item.invalid_optional_fields


def test_explicit_null_finish_obligations_is_malformed_but_absence_is_valid(tmp_path) -> None:
    write_item(tmp_path, "work/feature-a", _BASE + "finish_obligations:\n")
    (item,) = load_written_items(tmp_path)
    assert item.finish_obligations == ()
    assert "finish_obligations" in item.invalid_optional_fields

    write_item(tmp_path, "work/feature-a", _BASE)
    (absent,) = load_written_items(tmp_path)
    assert absent.finish_obligations == ()
    assert "finish_obligations" not in absent.invalid_optional_fields


SHA = "0123456789abcdef0123456789abcdef01234567"


def _sha_item(tmp_path, extra: str) -> WorkItem:
    write_item(tmp_path, "work/feature-a", _BASE + extra)
    (item,) = load_written_items(tmp_path)
    return item


def test_a_scalar_start_sha_is_projected(tmp_path) -> None:
    item = _sha_item(tmp_path, f"start_sha: {SHA}\n")
    assert item.start_sha == SHA
    assert "start_sha" not in item.invalid_optional_fields


@pytest.mark.parametrize("raw", ["abc", "0123456789ABCDEF0123456789ABCDEF01234567", "''", "[1]"])
def test_a_malformed_scalar_start_sha_is_flagged_not_raised(tmp_path, raw: str) -> None:
    item = _sha_item(tmp_path, f"start_sha: {raw}\n")
    assert item.start_sha is None
    assert "start_sha" in item.invalid_optional_fields


def test_a_repo_stamp_may_carry_a_start_sha(tmp_path) -> None:
    item = _sha_item(tmp_path, f"repo_stamps:\n  ui:\n    worktree: /wt/ui\n    branch: b\n    start_sha: {SHA}\n")
    assert item.repo_stamps["ui"].start_sha == SHA
    assert "repo_stamps" not in item.invalid_optional_fields


def test_a_repo_stamp_without_start_sha_still_loads(tmp_path) -> None:
    item = _sha_item(tmp_path, "repo_stamps:\n  ui:\n    worktree: /wt/ui\n    branch: b\n")
    assert item.repo_stamps["ui"].start_sha is None
    assert "repo_stamps" not in item.invalid_optional_fields


def test_a_repo_stamp_with_a_bad_start_sha_is_malformed(tmp_path) -> None:
    item = _sha_item(tmp_path, "repo_stamps:\n  ui:\n    worktree: /wt/ui\n    branch: b\n    start_sha: nope\n")
    assert "ui" not in item.repo_stamps
    assert "repo_stamps" in item.invalid_optional_fields


def test_commit_oid_accepts_sha1_and_sha256_only() -> None:
    assert is_commit_oid(SHA) and is_commit_oid("a" * 64)
    assert not any(is_commit_oid(v) for v in ("a" * 39, "a" * 41, "A" * 40, None, 1))


@pytest.mark.parametrize(
    ("authored", "expected", "invalid"),
    [
        (None, None, False),
        ({"code": "a" * 40, "workspace": "b" * 40}, SpecBaseline("a" * 40, "b" * 40), False),
        ({"workspace": "b" * 40}, SpecBaseline(workspace="b" * 40), False),
        ({"code": "a" * 64}, SpecBaseline(code="a" * 64), False),
        ({}, SpecBaseline(), False),
        ({"code": "abc123"}, None, True),
        ({"code": "A" * 40}, None, True),
        ({"code": "a" * 40, "extra": "b" * 40}, None, True),
        ("5c3c4fcea", None, True),
        (["a" * 40], None, True),
        ({"code": None}, None, True),
        ({"workspace": 42}, None, True),
    ],
)
def test_spec_baseline_coercion(authored, expected, invalid) -> None:
    baseline, malformed = spec_baseline_of(authored)
    assert baseline == expected
    assert malformed is invalid


@pytest.mark.parametrize("malformed", [False, True])
def test_spec_baseline_projects_from_page(tmp_path, malformed) -> None:
    baseline = (
        "spec_baseline: nope\n" if malformed else f"spec_baseline:\n  code: {'a' * 40}\n  workspace: {'b' * 40}\n"
    )
    write_item(tmp_path, "work/feature-baseline", "type: Feature\n" + baseline)
    item = load_written_items(tmp_path)[0]
    assert item.spec_baseline == (None if malformed else SpecBaseline("a" * 40, "b" * 40))
    assert ("spec_baseline" in item.invalid_optional_fields) is malformed

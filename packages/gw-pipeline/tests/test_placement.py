"""Pure placement preserves branch identities and repository-local owner chains."""

from dataclasses import replace
from itertools import pairwise

import pytest
from gw_pipeline import placement
from okf_io import load_bundle
from work_tracker_okf.items import load_items
from work_tracker_okf.vocabulary import SLUG_PREFIXES


def _items(root, *entries):
    for path, kind in entries:
        page = root / f"{path}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            f"---\ntype: {kind}\ntitle: Placement\ndescription: d\nstatus: stable\n"
            "work_status: open\nphase: design\naffects: []\n"
            "opened: 2026-10-22\nupdated: 2026-10-22\n---\n\n## Summary\n\nd\n",
            encoding="utf-8",
            newline="\n",
        )
    return load_items(load_bundle(root)).by_path


def _context(root, **overrides):
    return {
        "repo_name": "code",
        "repo_root": root / "repo",
        "working_checkout": root / "checkout",
        "worktrees_dir": root / "worktrees",
        "default_base": "main",
        **overrides,
    }


# Literal outputs recorded from orchestrate.commands before moving the helpers.
@pytest.mark.parametrize(
    ("kind", "path", "expected"),
    [
        ("Release", "work/epic-x/children/release-placement-verb", "release/placement-verb-fc0bb4d4"),
        ("Epic", "work/epic-x/children/epic-placement-verb", "epic/placement-verb-c5e0cec7"),
        ("Feature", "work/epic-x/children/feature-placement-verb", "feature/placement-verb-467efa51"),
        ("Bug", "work/epic-x/children/bug-placement-verb", "bug/placement-verb-32860e26"),
        ("TechDebt", "work/epic-x/children/tech-debt-placement-verb", "tech-debt/placement-verb-591944d9"),
        ("TestGap", "work/epic-x/children/test-gap-placement-verb", "test-gap/placement-verb-f7bef835"),
        ("Spike", "work/epic-x/children/spike-placement-verb", "spike/placement-verb-58a414f5"),
        ("Unknown", "work/work-placement-verb", "work/placement-verb-2d600d88"),
        ("Feature", "work/epic-feature-x", "feature/x-5a329cd4"),
        ("Bug", "work/feature-x", "bug/feature-x-04f8ef90"),
    ],
)
def test_branch_name_unchanged_for_every_type(kind, path, expected):
    assert kind in SLUG_PREFIXES or kind == "Unknown"
    assert placement.branch_name(path, kind) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("work/_archive/epic-x/children/feature-y", "work/epic-x/children/feature-y"),
        ("work/epic-x/children/_archive/feature-y", "work/epic-x/children/_archive/feature-y"),
        ("work/feature-y", "work/feature-y"),
        ("elsewhere/_archive/feature-y", "elsewhere/_archive/feature-y"),
        ("_archive/work/feature-y", "_archive/work/feature-y"),
        ("", ""),
    ],
)
def test_active_path_strips_only_the_work_archive_segment(path, expected):
    assert placement.active_path(path) == expected


def test_archived_item_keeps_its_branch(tmp_path):
    active = "work/epic-x/children/feature-y"
    archived = "work/_archive/epic-x/children/feature-y"
    items = _items(tmp_path / "bundle", (active, "Feature"), (archived, "Feature"))
    before = placement.placement_for(items[active], items, **_context(tmp_path))
    after = placement.placement_for(items[archived], items, **_context(tmp_path))
    assert before is not None and after is not None
    assert after.path == archived and after.active_path == active
    assert after.branch == before.branch and after.worktree == before.worktree
    assert after.slug == "feature-y"


def test_worktree_path_is_two_levels(tmp_path):
    path = "work/feature-x"
    items = _items(tmp_path / "bundle", (path, "Feature"))
    result = placement.placement_for(items[path], items, **_context(tmp_path))
    assert result is not None
    assert result.worktree == tmp_path / "worktrees" / "code" / "feature" / "x-04f8ef90"
    assert result.worktree != tmp_path / "worktrees" / "code" / "main"
    assert result.type == "Feature" and result.repo_name == "code"
    assert result.repo_root == tmp_path / "repo"


def test_top_level_parent_is_default_base(tmp_path):
    path = "work/feature-x"
    items = _items(tmp_path / "bundle", (path, "Feature"))
    result = placement.placement_for(items[path], items, **_context(tmp_path, default_base="develop"))
    assert result is not None
    assert result.parent_branch == "develop"
    assert result.parent_worktree == tmp_path / "checkout"
    assert result.owner_path is None


def test_missing_working_checkout_uses_repository_root(tmp_path):
    path = "work/feature-x"
    items = _items(tmp_path / "bundle", (path, "Feature"))
    result = placement.placement_for(items[path], items, **_context(tmp_path, working_checkout=None))
    assert result is not None
    assert result.parent_worktree == tmp_path / "repo"


@pytest.mark.parametrize("repo_name", ["code", "other"])
def test_owner_chain_two_levels_in_given_repo(tmp_path, repo_name):
    epic = "work/epic-x"
    feature = f"{epic}/children/feature-y"
    leaf = f"{feature}/children/bug-z"
    items = _items(tmp_path / "bundle", (epic, "Epic"), (feature, "Feature"), (leaf, "Bug"))
    chain = placement.placement_chain(items[leaf], items, **_context(tmp_path, repo_name=repo_name))
    assert [member.path for member in chain] == [epic, feature, leaf]
    assert [member.owner_path for member in chain] == [None, epic, feature]
    assert chain[0].parent_branch == "main"
    assert chain[0].parent_worktree == tmp_path / "checkout"
    for parent, child in pairwise(chain):
        assert child.parent_branch == parent.branch
        assert child.parent_worktree == parent.worktree
    assert all(member.worktree.is_relative_to(tmp_path / "worktrees" / repo_name) for member in chain)
    assert all(member.repo_root == tmp_path / "repo" for member in chain)


def test_plain_feature_parent_is_not_an_owner(tmp_path):
    parent = "work/feature-y"
    leaf = f"{parent}/children/bug-z"
    items = _items(tmp_path / "bundle", (parent, "Feature"), (leaf, "Bug"))
    # Tolerant snapshots may contain a parent whose child inventory is absent.
    items = {**items, parent: replace(items[parent], child_paths=())}
    chain = placement.placement_chain(items[leaf], items, **_context(tmp_path))
    assert len(chain) == 1 and chain[0].path == leaf
    assert chain[0].owner_path is None and chain[0].parent_branch == "main"


def test_archived_owner_uses_active_branch(tmp_path):
    epic = "work/epic-x"
    leaf = f"{epic}/children/bug-z"
    archived_epic = "work/_archive/epic-x"
    archived_leaf = f"{archived_epic}/children/bug-z"
    items = _items(tmp_path / "bundle", (epic, "Epic"), (leaf, "Bug"), (archived_epic, "Epic"), (archived_leaf, "Bug"))
    before = placement.placement_chain(items[leaf], items, **_context(tmp_path))
    after = placement.placement_chain(items[archived_leaf], items, **_context(tmp_path))
    assert [member.branch for member in before] == [member.branch for member in after]
    assert before[-1].parent_worktree == after[-1].parent_worktree
    assert after[-1].owner_path == archived_epic


@pytest.mark.parametrize("missing", ["repo_name", "repo_root"])
def test_workspace_only_item_is_none(tmp_path, missing):
    path = "work/feature-x"
    items = _items(tmp_path / "bundle", (path, "Feature"))
    context = _context(tmp_path, **{missing: None})
    assert placement.placement_for(items[path], items, **context) is None
    assert placement.placement_chain(items[path], items, **context) == ()


def test_null_notifier_note(tmp_path):
    event = placement.PlacementEvent(
        repo_root=tmp_path / "repo",
        worktree=tmp_path / "worktree",
        parent_worktree=tmp_path / "checkout",
        slug="feature-x",
        worktrees_dir=tmp_path,
    )
    notifier: placement.PlacementNotifier = placement.NullNotifier()
    assert notifier.placed(event) == "orca: not configured"

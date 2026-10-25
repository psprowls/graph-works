"""`item_placement` resolves an item's placement in one repository, legacy stamps first."""

import subprocess
from pathlib import Path

import pytest
from gw_kernel.workspace.layout import layout_for
from gw_kernel.workspace.provenance import default_base
from gw_pipeline.placement import placement_for
from gw_pipeline.placement_resolve import item_placement
from okf_io import load_bundle
from work_tracker_okf.items import load_items


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init", "-b", "main"], check=True, capture_output=True)
    return path.resolve()


def _setup(tmp_path, repos=("code",), pages=None):
    root = tmp_path / "ws"
    root.mkdir()
    layout = layout_for(root)
    decl = "".join(f"  {name}: {{path: {_repo(tmp_path / name)}}}\n" for name in repos)
    layout.manifest_path.write_text(
        "version: 1\nworkflow_config: workflow.yaml\n" + (f"repositories:\n{decl}" if decl else ""), encoding="utf-8"
    )
    for path, body in (pages or {}).items():
        page = layout.bundle_dir / f"{path}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        typ = "Epic" if "epic-" in path else "Feature"
        page.write_text(f"---\ntype: {typ}\ntitle: T\ndescription: d\n{body}---\n", encoding="utf-8", newline="\n")
    items = load_items(load_bundle(layout.bundle_dir)).by_path
    return layout, items


EPIC = "work/epic-e"
CHILD = "work/epic-e/children/feature-c"
TOP = "work/feature-top"
CHILD_PAGE = "work/epic-e/children/feature-c"


def _pages(epic="", child="", top=""):
    return {EPIC: epic, "work/epic-e/children/feature-c": child, TOP: top}


def _expected(layout, item, items, name="code"):
    repo = layout.manifest_path.parent.parent / name
    return placement_for(
        item,
        items,
        repo_name=name,
        repo_root=repo.resolve(),
        working_checkout=repo.resolve(),
        worktrees_dir=layout.worktrees_dir,
        default_base=default_base(repo.resolve()),
    )


def test_top_level_item_parent_is_working_checkout(tmp_path):
    layout, items = _setup(tmp_path, pages=_pages())
    got = item_placement(layout, items[TOP], items)
    repo = (tmp_path / "code").resolve()
    assert got is not None
    assert got.parent_branch == default_base(repo)
    assert got.parent_worktree == repo
    assert got.worktree == layout.worktrees_dir / "code" / got.branch


def test_nested_item_parent_is_owner_worktree(tmp_path):
    layout, items = _setup(tmp_path, pages=_pages())
    child = item_placement(layout, items[CHILD], items)
    epic = item_placement(layout, items[EPIC], items)
    assert child is not None and epic is not None
    assert child.parent_branch == epic.branch
    assert child.parent_worktree == epic.worktree


def test_foreign_repo_name(tmp_path):
    layout, items = _setup(tmp_path, repos=("code", "other"), pages=_pages(epic="repo: code\n"))
    got = item_placement(layout, items[TOP], items, repo_name="other")
    assert got is not None
    assert got.repo_name == "other"
    assert got.worktree == layout.worktrees_dir / "other" / got.branch


def test_no_repo_is_none(tmp_path):
    layout, items = _setup(tmp_path, repos=(), pages=_pages())
    assert item_placement(layout, items[TOP], items) is None


def test_legacy_item_and_owner_win(tmp_path):
    layout, items = _setup(
        tmp_path,
        pages=_pages(
            epic="worktree: /orca/epic\nbranch: psprowls/epic-orca\n",
            child="worktree: /orca/child\nbranch: psprowls/child-orca\n",
        ),
    )
    got = item_placement(layout, items[CHILD], items)
    assert got is not None
    assert (got.worktree, got.branch) == (Path("/orca/child"), "psprowls/child-orca")
    assert (got.parent_worktree, got.parent_branch) == (Path("/orca/epic"), "psprowls/epic-orca")


def test_legacy_item_and_owner_win_with_repo_name_and_no_declared_repo(tmp_path):
    layout, items = _setup(
        tmp_path,
        pages=_pages(
            epic="worktree: /orca/epic\nbranch: psprowls/epic-orca\n",
            child="worktree: /orca/child\nbranch: psprowls/child-orca\n",
        ),
    )
    got = item_placement(layout, items[CHILD], items, repo_name="code")
    assert got is not None
    assert (got.worktree, got.branch) == (Path("/orca/child"), "psprowls/child-orca")
    assert (got.parent_worktree, got.parent_branch) == (Path("/orca/epic"), "psprowls/epic-orca")


def test_stampless_child_under_stamped_epic(tmp_path):
    layout, items = _setup(tmp_path, pages=_pages(epic="worktree: /orca/epic\nbranch: psprowls/epic-orca\n"))
    got = item_placement(layout, items[CHILD], items)
    computed = _expected(layout, items[CHILD], items)
    assert got is not None and computed is not None
    assert (got.worktree, got.branch) == (computed.worktree, computed.branch)
    assert (got.parent_worktree, got.parent_branch) == (Path("/orca/epic"), "psprowls/epic-orca")


def test_unknown_repo_name_raises(tmp_path):
    from gw_kernel.workspace.errors import WorkspaceError

    layout, items = _setup(tmp_path, pages=_pages())
    with pytest.raises(WorkspaceError):
        item_placement(layout, items[TOP], items, repo_name="nope")

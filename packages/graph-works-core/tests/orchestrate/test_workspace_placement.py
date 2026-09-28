"""Pure workspace-branch selection for orchestration."""

from dataclasses import replace

from graph_works_core.orchestrate.anchors import (
    AnchorRefusal,
    WorkspacePlacement,
    WorkspacePreparation,
    select_workspace,
    verify_workspace_stamp,
    workspace_chain,
    workspace_worktree_path,
)
from graph_works_core.orchestrate.commands import branch_name
from graph_works_core.workspace.repo_context import RepositoryContext
from test_orchestrate_plan import _item
from work_tracker_okf.items import Stamp, WorkItem

ROOT = "work/epic-a"
CHILD = f"{ROOT}/children/feature-a"
WT = "/ws/.gw/worktrees"


def ws_context(**inventory: tuple[str, ...]) -> RepositoryContext:
    paths = {path for checkouts in inventory.values() for path in checkouts}
    return RepositoryContext(
        "git-ws",
        "/ws",
        "main",
        True,
        {"main": ("/ws",), **inventory},
        {path: True for path in paths},
        True,
        {"/ws": True, **{path: True for path in paths}},
        branches=frozenset({"main", *inventory}),
    )


def by_path(*items: WorkItem) -> dict[str, WorkItem]:
    return {item.path: item for item in items}


def test_chain_is_outermost_first() -> None:
    epic = _item(ROOT, type="Epic", phase="execute", child_paths=(CHILD,))
    child = _item(CHILD, phase="execute")
    assert [item.path for item in workspace_chain(child, by_path(epic, child))] == [ROOT, CHILD]


def test_worktree_path_uses_stable_stem() -> None:
    path = workspace_worktree_path(WT, CHILD, "Feature")
    assert path.startswith(f"{WT}/workspace/")
    assert path.endswith(branch_name(CHILD, "Feature").split("/", 1)[1])


def test_missing_epic_anchor_is_prepared_first_from_main() -> None:
    epic = _item(ROOT, type="Epic", phase="execute", child_paths=(CHILD,))
    child = _item(CHILD, phase="execute")
    got = select_workspace(child, items=by_path(epic, child), context=ws_context(), worktrees_dir=WT)
    assert got == WorkspacePreparation(
        ROOT, "execute", workspace_worktree_path(WT, ROOT, "Epic"), branch_name(ROOT, "Epic"), "main"
    )


def test_child_is_prepared_from_verified_epic_anchor() -> None:
    anchor = workspace_worktree_path(WT, ROOT, "Epic")
    epic = _item(
        ROOT,
        type="Epic",
        phase="execute",
        child_paths=(CHILD,),
        repo_stamps={"_workspace": Stamp(anchor, branch_name(ROOT, "Epic"))},
    )
    child = _item(CHILD, phase="execute")
    context = ws_context(**{branch_name(ROOT, "Epic"): (anchor,)})
    got = select_workspace(child, items=by_path(epic, child), context=context, worktrees_dir=WT)
    assert got == WorkspacePreparation(
        CHILD,
        "execute",
        workspace_worktree_path(WT, CHILD, "Feature"),
        branch_name(CHILD, "Feature"),
        branch_name(ROOT, "Epic"),
    )


def test_fully_stamped_chain_places_item() -> None:
    anchor = workspace_worktree_path(WT, ROOT, "Epic")
    own = workspace_worktree_path(WT, CHILD, "Feature")
    epic = _item(
        ROOT,
        type="Epic",
        phase="execute",
        child_paths=(CHILD,),
        repo_stamps={"_workspace": Stamp(anchor, branch_name(ROOT, "Epic"))},
    )
    child = _item(CHILD, phase="execute", repo_stamps={"_workspace": Stamp(own, branch_name(CHILD, "Feature"))})
    context = ws_context(**{branch_name(ROOT, "Epic"): (anchor,), branch_name(CHILD, "Feature"): (own,)})
    got = select_workspace(child, items=by_path(epic, child), context=context, worktrees_dir=WT)
    assert got == WorkspacePlacement(own, branch_name(CHILD, "Feature"))


def test_unverified_or_dirty_stamp_refuses() -> None:
    own = workspace_worktree_path(WT, CHILD, "Feature")
    child = _item("work/feature-lone", phase="execute", repo_stamps={"_workspace": Stamp(own, "feature/x")})
    missing = select_workspace(child, items=by_path(child), context=ws_context(), worktrees_dir=WT)
    assert isinstance(missing, AnchorRefusal) and missing.kind == "worktree-unprovable"
    dirty_context = replace(ws_context(**{"feature/x": (own,)}), checkout_usable_by_path={"/ws": True, own: False})
    dirty = select_workspace(child, items=by_path(child), context=dirty_context, worktrees_dir=WT)
    assert isinstance(dirty, AnchorRefusal) and "dirty" in dirty.reason


def test_ambiguous_workspace_branch_refuses() -> None:
    own = workspace_worktree_path(WT, CHILD, "Feature")
    child = _item("work/feature-lone", repo_stamps={"_workspace": Stamp(own, "feature/x")})
    context = ws_context(**{"feature/x": (own, "/other")})
    got = verify_workspace_stamp(child, context)
    assert isinstance(got, AnchorRefusal) and got.kind == "worktree-ambiguous"


def test_malformed_repo_stamps_refuses_even_without_workspace_stamp() -> None:
    child = _item("work/feature-lone", invalid_optional_fields=frozenset({"repo_stamps"}))
    got = verify_workspace_stamp(child, ws_context())
    assert isinstance(got, AnchorRefusal) and got.kind == "worktree-unprovable"

"""An item's placement in one repository, for finish, the gate and cleanup."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from gw_kernel.workspace.errors import WorkspaceError
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.provenance import default_base
from gw_kernel.workspace.repos import ItemRepo, declared_repositories, resolve_item_repo
from work_tracker_okf.hierarchy import declared_repo
from work_tracker_okf.items import WorkItem

from gw_pipeline.placement import EffectivePlacement, Placement, effective_placement, enclosing_owner, placement_for


def _legacy(
    item: WorkItem,
    items: Mapping[str, WorkItem],
    repo: ItemRepo,
    own_repo_name: str | None,
    worktrees_dir: Path,
    base: str,
) -> EffectivePlacement | None:
    found = effective_placement(
        item, items, repo=repo, own_repo_name=own_repo_name, worktrees_dir=worktrees_dir, default_base=base
    )
    return found if found is not None and found.source == "legacy" else None


def item_placement(
    layout: WorkspaceLayout,
    item: WorkItem,
    items: Mapping[str, WorkItem],
    *,
    repo_name: str | None = None,
) -> Placement | None:
    """Where *item* lives in its resolved repository (or the declared *repo_name*); `None` when none resolves.

    A legacy stored stamp on the item or its enclosing owner wins over the computed value.
    `WorkspaceError` from repository resolution propagates.
    """
    declared_name = declared_repo(item, items)[0]
    foreign = repo_name is not None and declared_name is not None and declared_name != repo_name
    if foreign:
        # A foreign repository (an owner finishing into a repo its descendant used): `repo:` names the item's
        # own repository, not a constraint on where a receipt entry may point.
        declared = declared_repositories(layout)
        if repo_name not in declared:
            raise WorkspaceError(f"{item.path}: repository {repo_name!r} is not declared in {layout.manifest_path}")
        own = ItemRepo(repo_name, declared[repo_name], "flag")
    else:
        own = resolve_item_repo(layout, item, items, repo_name=repo_name)
    if own.name is None or own.path is None:
        return None
    base = default_base(own.path)
    placement = placement_for(
        item,
        items,
        repo_name=own.name,
        repo_root=own.path,
        working_checkout=own.path,
        worktrees_dir=layout.worktrees_dir,
        default_base=base,
    )
    if placement is None:
        return None
    own_repo_name = declared_name if foreign else own.name
    mine = _legacy(item, items, own, own_repo_name, layout.worktrees_dir, base)
    if mine is not None:
        placement = replace(placement, worktree=Path(mine.worktree), branch=mine.branch)
    owner = enclosing_owner(item, items)
    if owner is not None:
        theirs = _legacy(owner, items, own, own_repo_name, layout.worktrees_dir, base)
        if theirs is not None:
            placement = replace(placement, parent_worktree=Path(theirs.worktree), parent_branch=theirs.branch)
    return placement


def worktree_missing_detail(path: str, placement: Placement) -> str:
    """The one `worktree-missing` refusal detail, shared by the gate and the commit gate."""
    return (
        f"{path}: {placement.worktree} is not a checkout of {placement.repo_name} on {placement.branch}; "
        f"run gw work place {path}"
    )

"""An item's placement in one repository, for finish, the gate and cleanup."""

from __future__ import annotations

from collections.abc import Mapping

from gw_kernel.workspace.errors import WorkspaceError
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.provenance import default_base
from gw_kernel.workspace.repos import ItemRepo, declared_repositories, resolve_item_repo
from work_tracker_okf.hierarchy import declared_repo
from work_tracker_okf.items import WorkItem

from gw_pipeline.placement import Placement, placement_for


def item_placement(
    layout: WorkspaceLayout,
    item: WorkItem,
    items: Mapping[str, WorkItem],
    *,
    repo_name: str | None = None,
) -> Placement | None:
    """Where *item* lives in its resolved repository (or the declared *repo_name*); `None` when none resolves.

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
    return placement


def worktree_missing_detail(path: str, placement: Placement) -> str:
    """The one `worktree-missing` refusal detail, shared by the gate and the commit gate."""
    return (
        f"{path}: {placement.worktree} is not a checkout of {placement.repo_name} on {placement.branch}; "
        f"run gw work place {path}"
    )

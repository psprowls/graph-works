"""Complete repository-local finish targets shared by attended and relay routing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from work_tracker_okf.items import WorkItem

from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repo_context import RepositoryContext, observe_repository
from graph_works_core.workspace.repos import ItemRepo, declared_repositories, resolve_item_repo


@dataclass(frozen=True, slots=True)
class FinishTarget:
    repo: ItemRepo
    worktree: str
    source_branch: str
    target_branch: str


@dataclass(frozen=True, slots=True)
class FinishPlan:
    targets: tuple[FinishTarget, ...]
    blockers: tuple[str, ...]


def enclosing_owner(item: WorkItem, items: Mapping[str, WorkItem]) -> WorkItem | None:
    parent = item.parent_path
    seen: set[str] = set()
    while parent and parent not in seen:
        seen.add(parent)
        owner = items.get(parent)
        if owner is None:
            return None
        if owner.type in {"Epic", "Release"} or (
            owner.type == "Feature" and (owner.active_child_paths or owner.archived_child_paths)
        ):
            return owner
        parent = owner.parent_path
    return None


def resolve_finish_targets(
    layout: WorkspaceLayout,
    items: Sequence[WorkItem],
    path: str,
    *,
    single_repo: ItemRepo | None = None,
    repo_contexts: Mapping[str, RepositoryContext] = MappingProxyType({}),
) -> FinishPlan:
    """Read and validate all owned stamps; no placement or Git mutation.

    `single_repo` preserves the explicit Python repository override without
    assigning any foreign stamps. The orchestration shell may supply its
    already-observed `repo_contexts` to avoid probing those repositories twice.
    """
    by_path = {item.path: item for item in items}
    item = by_path[path]
    targets: list[FinishTarget] = []
    blockers: list[str] = []
    if "repo_stamps" in item.invalid_optional_fields:
        blockers.append(f"{path}: repair malformed repo_stamps before finishing")
    if single_repo is not None and item.repo_stamps:
        return FinishPlan((), (f"{path}: explicit repo override cannot finish foreign repo_stamps",))
    observed = dict(repo_contexts)

    def context_for(repo: ItemRepo) -> RepositoryContext:
        assert repo.path is not None
        context = next((c for c in observed.values() if str(repo.path) in c.checkout_usable_by_path), None)
        if context is None:
            context = observe_repository(repo.path)
            observed[context.identity] = context
        return context

    candidates: list[tuple[ItemRepo, str | None, str | None]] = []
    try:
        declared = declared_repositories(layout)
        if item.branch or item.worktree:
            candidates.append((single_repo or resolve_item_repo(layout, item, by_path), item.worktree, item.branch))
        elif not item.repo_stamps and item.type not in {"Epic", "Release"}:
            # Main-mode leaves may finish without owning a dedicated branch.
            # The declared checkout is the compatibility location, but its
            # current branch must be observed rather than inferred from trunk.
            own_repo = single_repo or resolve_item_repo(layout, item, by_path)
            if own_repo.path is None:
                blockers.append(f"{path}: no repository checkout can be verified for unstamped finish")
            else:
                checkout = str(own_repo.path.resolve())
                context = context_for(own_repo)
                branches = [branch for branch, paths in context.inventory.items() if checkout in paths]
                if len(branches) != 1:
                    blockers.append(f"{path}: cannot verify current branch of unstamped finish checkout {checkout!r}")
                else:
                    candidates.append((own_repo, checkout, branches[0]))
        for name, stamp in sorted(item.repo_stamps.items()):
            if name not in declared:
                blockers.append(f"{path}: stamped repo {name!r} is not declared")
            else:
                candidates.append((ItemRepo(name, declared[name], "frontmatter"), stamp.worktree, stamp.branch))
        outer = enclosing_owner(item, by_path)
        outer_repo = (single_repo or resolve_item_repo(layout, outer, by_path)) if outer is not None else None
    except WorkspaceError as exc:
        return FinishPlan((), (str(exc),))
    for repo, worktree, branch in candidates:
        if repo.path is None or not worktree or not branch:
            blockers.append(f"{path}: repair incomplete finish stamp for {repo.name!r}")
            continue
        worktree = str(Path(worktree).resolve())
        context = context_for(repo)
        if (
            not context.identity_known
            or not context.inventory_known
            or context.inventory.get(branch) != (worktree,)
            or context.path_exists.get(worktree) is not True
            or context.checkout_usable_by_path.get(worktree) is not True
        ):
            blockers.append(f"{path}: cannot verify clean worktree {worktree!r} on branch {branch!r} in {repo.name!r}")
            continue
        target: str | None = context.default_base
        if outer is not None:
            outer_stamp = outer.repo_stamps.get(repo.name) if repo.name is not None else None
            own = outer_repo is not None and outer_repo.name == repo.name
            anchor_path, target = (
                (outer.worktree, outer.branch)
                if own
                else ((outer_stamp.worktree, outer_stamp.branch) if outer_stamp else (None, None))
            )
            if not anchor_path or not target:
                blockers.append(
                    f"{path}: prepare enclosing integration anchor {outer.path} in {repo.name!r} before finish"
                )
                continue
            anchor_path = str(Path(anchor_path).resolve())
            if (
                "repo_stamps" in outer.invalid_optional_fields
                or context.inventory.get(target) != (anchor_path,)
                or context.path_exists.get(anchor_path) is not True
                or context.checkout_usable_by_path.get(anchor_path) is not True
            ):
                blockers.append(f"{path}: repair enclosing integration anchor {outer.path} in {repo.name!r}")
                continue
        if not target or not context.branches_known or target not in context.branches:
            blockers.append(f"{path}: cannot verify target branch {target!r} in {repo.name!r}")
            continue
        targets.append(FinishTarget(repo, worktree, branch, target))
    return FinishPlan(tuple(targets), tuple(blockers))

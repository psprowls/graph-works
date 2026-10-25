"""Resolve work items and repositories for the placement engine."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date
from pathlib import Path

from gw_kernel.workspace.bundle import load_work_bundle
from gw_kernel.workspace.errors import WorkspaceError
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.provenance import GitExecutable, GitFailure, default_base, gate_git
from gw_kernel.workspace.repos import ItemRepo, resolve_item_repo
from gw_pipeline import place as engine
from gw_pipeline.place import PlaceContext, PlaceMode, PlaceRefusal, PlaceResult, PruneCandidate
from gw_pipeline.placement import Placement, PlacementNotifier, active_path, placement_chain
from work_tracker_okf.affects import code_affects, touches_workspace
from work_tracker_okf.items import WorkItem, load_items


def _empty(
    path: str | None,
    mode: PlaceMode,
    dry_run: bool,
    *,
    refusal: PlaceRefusal | None = None,
    warnings: tuple[str, ...] = (),
) -> PlaceResult:
    return PlaceResult(path, mode, dry_run, None, (), None, None, (), None, refusal, warnings)


def _repository(
    layout: WorkspaceLayout, item: WorkItem, items: Mapping[str, WorkItem], repo_name: str | None
) -> ItemRepo:
    if touches_workspace(item.affects) and not code_affects(item.affects):
        return ItemRepo(
            None, None, "sole", f"{item.path} affects only the workspace; it runs in the workspace primary checkout"
        )
    return resolve_item_repo(layout, item, items, repo_name=repo_name)


def _chain(
    layout: WorkspaceLayout, item: WorkItem, items: Mapping[str, WorkItem], own: ItemRepo, git: GitExecutable
) -> tuple[Placement, ...]:
    return placement_chain(
        item,
        items,
        repo_name=own.name,
        repo_root=own.path,
        working_checkout=own.path,
        worktrees_dir=layout.worktrees_dir,
        default_base=default_base(own.path, git=git),
    )


def _no_repo(item: WorkItem, own: ItemRepo) -> str:
    return "no-repo: " + (
        own.note or f"{item.path} resolves to no code repository; it runs in the workspace primary checkout"
    )


def _prune(ctx: PlaceContext, path: str | None, items: Mapping[str, WorkItem], repo_name: str | None) -> PlaceResult:
    root = active_path(path) if path is not None else None
    subtree = {
        key: item
        for key, item in items.items()
        if root is None or active_path(key) == root or active_path(key).startswith(root + "/")
    }
    warnings: list[str] = []
    resolved: dict[str, ItemRepo] = {}
    for item in subtree.values():
        try:
            own = _repository(ctx.layout, item, items, repo_name if item.path == path else None)
        except WorkspaceError as exc:
            warnings.append(f"{item.path}: skipped repository resolution: {exc}")
            continue
        if own.name is None:
            warnings.append(_no_repo(item, own))
            continue
        resolved[item.path] = own
    candidates: list[PruneCandidate] = []
    acting: Placement | None = None
    for item in subtree.values():
        direct = resolved.get(item.path)
        repositories = {direct.name: direct} if direct is not None else {}
        # Compare active paths so archived descendants still contribute lazy
        # owner placements even when their physical parent page has moved.
        if item.type in {"Epic", "Release", "Feature"}:
            prefix = active_path(item.path) + "/"
            for descendant, descendant_repo in resolved.items():
                if active_path(descendant).startswith(prefix):
                    repositories[descendant_repo.name] = descendant_repo
        for repository in repositories.values():
            placement = _chain(ctx.layout, item, items, repository, ctx.git)[-1]
            candidates.append(PruneCandidate(item, placement))
            if item.path == path and direct is not None and repository.name == direct.name:
                acting = placement
    ordered = sorted(
        candidates, key=lambda row: (-active_path(row.item.path).count("/"), row.item.path, row.placement.repo_name)
    )
    result = engine.prune(ctx, ordered, path=path)
    return replace(result, placement=acting, warnings=(*result.warnings, *warnings))


def run_place(
    layout: WorkspaceLayout,
    path: str | None,
    *,
    mode: PlaceMode,
    dry_run: bool,
    repo_name: str | None,
    today: date,
    notifier: PlacementNotifier,
    occupied: Callable[[Path], str | None] | None = None,
    environ: Mapping[str, str] | None = None,
) -> PlaceResult:
    """Resolve one item's owner chain, or descendant-first candidates for pruning."""
    if path is None and mode != "prune":
        raise ValueError(f"path is required for placement mode {mode}")
    items = load_items(load_work_bundle(layout)).by_path
    item = items.get(path) if path is not None else None
    if path is not None and item is None:
        return _empty(path, mode, dry_run, refusal=PlaceRefusal("unknown-item", f"no work item at {path}"))
    git = gate_git(layout, environ=environ)
    if isinstance(git, GitFailure):
        return _empty(path, mode, dry_run, refusal=PlaceRefusal("git-unavailable", f"{git.cause}: {git.detail}"))
    ctx = PlaceContext(layout, git, notifier, today, dry_run, occupied)
    if mode == "prune":
        return _prune(ctx, path, items, repo_name)
    assert item is not None
    own = _repository(layout, item, items, repo_name)
    if own.name is None:
        return _empty(path, mode, dry_run, warnings=(_no_repo(item, own),))
    chain = _chain(layout, item, items, own, git)
    if mode == "remove":
        return engine.remove(ctx, chain[-1])
    if mode == "start":
        return engine.start(ctx, chain)
    return engine.ensure(ctx, chain)

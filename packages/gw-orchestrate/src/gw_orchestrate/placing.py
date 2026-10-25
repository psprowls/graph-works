"""The one adapter between orchestration and the gw-pipeline placement engine.

Every child-1 name (`placement_for`, `placement_chain`, `Placement`, the notifier, the engine's `ensure`/`start` and
its refusal vocabulary) is mapped here once. Items and the item's repository resolve exactly as `gw work place`
resolves them, but inside gw-orchestrate, which never imports gw-work.
"""

from __future__ import annotations

import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from gw_kernel.workspace.bundle import load_work_bundle
from gw_kernel.workspace.errors import WorkspaceError
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.provenance import GitExecutable, GitFailure, gate_git
from gw_kernel.workspace.provenance import default_base as _default_base
from gw_kernel.workspace.repos import ItemRepo, resolve_item_repo
from gw_pipeline import place as engine
from gw_pipeline.place import PlaceContext, PlaceRefusal, PlaceResult
from gw_pipeline.placement import Placement, PlacementNotifier, placement_chain, placement_for
from work_tracker_okf.affects import code_affects, touches_workspace
from work_tracker_okf.items import WorkItem, load_items

REFUSAL_KINDS: frozenset[str] = frozenset(typing.get_args(engine.RefusalKind))


@dataclass(frozen=True, slots=True)
class ChainStep:
    path: str
    worktree: str
    branch: str
    action: str
    refresh: str | None
    orca: str | None


@dataclass(frozen=True, slots=True)
class Realized:
    placement: Placement
    chain: tuple[ChainStep, ...]
    start_sha: str | None


@dataclass(frozen=True, slots=True)
class Refused:
    kind: str
    detail: str


def _repo_name(repo: ItemRepo) -> str | None:
    if repo.path is None:
        return None
    return repo.name or Path(repo.path).name


def compute(
    item: WorkItem, items: Mapping[str, WorkItem], repo: ItemRepo, *, worktrees_dir: Path, default_base: str
) -> Placement | None:
    """The item's computed placement, or `None` when it has no code repository."""
    name = _repo_name(repo)
    if name is None:
        return None
    return placement_for(
        item,
        items,
        repo_name=name,
        repo_root=repo.path,
        working_checkout=repo.path,
        worktrees_dir=worktrees_dir,
        default_base=default_base,
    )


def compute_chain(
    item: WorkItem, items: Mapping[str, WorkItem], repo: ItemRepo, *, worktrees_dir: Path, default_base: str
) -> tuple[Placement, ...]:
    """Enclosing owners outermost first, then the item; `()` when `compute` is `None`."""
    name = _repo_name(repo)
    if name is None:
        return ()
    return placement_chain(
        item,
        items,
        repo_name=name,
        repo_root=repo.path,
        working_checkout=repo.path,
        worktrees_dir=worktrees_dir,
        default_base=default_base,
    )


def _repository(
    layout: WorkspaceLayout, item: WorkItem, items: Mapping[str, WorkItem], repo_name: str | None
) -> ItemRepo:
    if touches_workspace(item.affects) and not code_affects(item.affects):
        return ItemRepo(None, None, "sole", f"{item.path} affects only the workspace")
    return resolve_item_repo(layout, item, items, repo_name=repo_name)


@dataclass(frozen=True, slots=True)
class Resolved:
    """The items read for *path* and its computed chain: owners outermost first, then the item."""

    items: Mapping[str, WorkItem]
    chain: tuple[Placement, ...]
    git: GitExecutable

    @property
    def placement(self) -> Placement | None:
        return self.chain[-1] if self.chain else None


def resolve(
    layout: WorkspaceLayout,
    path: str,
    *,
    repo_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Resolved | Refused:
    """Read the bundle and compute *path*'s placement chain now; `chain == ()` when it has no code repository.

    *repo_name* is the caller's selection hint among several declared repositories, as `resolve_item_repo` takes it.
    """
    try:
        items = load_items(load_work_bundle(layout)).by_path
    except WorkspaceError as exc:
        return Refused("unknown-item", str(exc))
    item = items.get(path)
    if item is None:
        return Refused("unknown-item", f"no work item at {path}")
    git = gate_git(layout, environ=environ)
    if isinstance(git, GitFailure):
        return Refused("git-unavailable", f"{git.cause}: {git.detail}")
    try:
        own = _repository(layout, item, items, repo_name)
    except WorkspaceError as exc:
        return Refused("unknown-item", str(exc))
    if _repo_name(own) is None:
        return Resolved(items, (), git)
    assert own.path is not None
    chain = compute_chain(
        item, items, own, worktrees_dir=layout.worktrees_dir, default_base=_default_base(own.path, git=git)
    )
    return Resolved(items, chain, git)


def _steps(result: PlaceResult) -> tuple[ChainStep, ...]:
    return tuple(
        ChainStep(
            s.placement.path,
            str(s.placement.worktree),
            s.placement.branch,
            s.action,
            s.refresh.kind if s.refresh is not None else None,
            s.orca,
        )
        for s in result.chain
    )


def realize(
    layout: WorkspaceLayout,
    path: str,
    *,
    phase: str,
    verify_only: bool,
    notifier: PlacementNotifier,
    occupied: Callable[[str], str | None],
    today: date | None = None,
    environ: Mapping[str, str] | None = None,
    repo_name: str | None = None,
) -> Realized | Refused:
    """Run the engine for *path*: `start` at execute, verify-only `ensure` on request, else `ensure`."""
    resolved = resolve(layout, path, repo_name=repo_name, environ=environ)
    if isinstance(resolved, Refused):
        return resolved
    if not resolved.chain:
        return Refused("unknown-item", f"{path} resolves to no code repository")
    ctx = PlaceContext(
        layout,
        resolved.git,
        notifier,
        today or date.today(),
        False,
        lambda p: occupied(str(p)),
    )
    if phase == "execute" and not verify_only:
        result = engine.start(ctx, resolved.chain)
    else:
        result = engine.ensure(ctx, resolved.chain, refresh=not verify_only)
    refusal: PlaceRefusal | None = result.refusal
    if refusal is not None:
        return Refused(refusal.kind, refusal.detail)
    assert result.placement is not None  # a non-empty chain always yields the item's placement
    sha = result.start_sha[1] if result.start_sha is not None else None
    return Realized(result.placement, _steps(result), sha)

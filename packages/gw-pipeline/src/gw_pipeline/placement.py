"""Pure repository-local branch, worktree and integration-owner placement."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from gw_kernel.workspace.repos import ItemRepo
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import SLUG_PREFIXES

#: The branch path segment for an item whose `type` is unrecognized. Reachable
#: only through the root's fallback branch name: a candidate with a bad `type`
#: is already blocked by the router's own validation.
_UNKNOWN_TYPE_SEGMENT = "work"


def _stable_stem(path: str, type_: str) -> str:
    """`"<basename minus its type prefix>-<sha256(path)[:8]>"`.

    The shared middle of `branch_name` and `session_name`. Extracted so a
    branch and the session that runs on it cannot drift apart: they are the
    same stem under two different prefixes.
    """
    segment = SLUG_PREFIXES.get(type_) or _UNKNOWN_TYPE_SEGMENT
    basename = PurePosixPath(path).name
    stripped = basename
    if segment != "epic" and stripped.startswith("epic-"):
        stripped = stripped[len("epic-") :]
    if stripped.startswith(f"{segment}-"):
        stripped = stripped[len(segment) + 1 :]
    suffix = hashlib.sha256(path.encode("utf-8")).hexdigest()[:8]
    return f"{stripped}-{suffix}"


def branch_name(path: str, type_: str) -> str:
    """Canonical path to a readable, collision-safe branch name.

    Uses the path-native basename and strips its type prefix into the branch
    segment. Legacy filename interpretation belongs only to the migration.

        epic-gw-kernel    (Epic)    -> epic/gw-kernel
        feature-work-pipeline-x (Feature) -> feature/work-pipeline-x
    """
    segment = SLUG_PREFIXES.get(type_) or _UNKNOWN_TYPE_SEGMENT
    return f"{segment}/{_stable_stem(path, type_)}"


def active_path(path: str) -> str:
    """Remove the work lane's archive marker without changing other segments."""
    parts = path.split("/")
    if parts[:2] == ["work", "_archive"]:
        return "/".join((parts[0], *parts[2:]))
    return path


def enclosing_owner(item: WorkItem, items: Mapping[str, WorkItem]) -> WorkItem | None:
    parent = item.parent_path
    seen: set[str] = set()
    while parent and parent not in seen:
        seen.add(parent)
        owner = items.get(parent)
        if owner is None:
            return None
        if owner.type in {"Epic", "Release"} or (owner.type == "Feature" and owner.child_paths):
            return owner
        parent = owner.parent_path
    return None


@dataclass(frozen=True, slots=True)
class Placement:
    path: str
    active_path: str
    type: str
    slug: str
    repo_name: str
    repo_root: Path
    branch: str
    worktree: Path
    parent_branch: str
    parent_worktree: Path
    owner_path: str | None


def placement_for(
    item: WorkItem,
    items: Mapping[str, WorkItem],
    *,
    repo_name: str | None,
    repo_root: Path | None,
    working_checkout: Path | None,
    worktrees_dir: Path,
    default_base: str,
) -> Placement | None:
    """Compute placement in the supplied repository without observing disk or git.

    An absent working checkout uses the repository root as the base checkout.
    """
    if repo_name is None or repo_root is None:
        return None
    path = active_path(item.path)
    branch = branch_name(path, item.type)
    owner = enclosing_owner(item, items)
    parent_branch = branch_name(active_path(owner.path), owner.type) if owner is not None else default_base
    parent_worktree = working_checkout if working_checkout is not None else repo_root
    if owner is not None:
        parent_worktree = worktrees_dir / repo_name / parent_branch
    return Placement(
        path=item.path,
        active_path=path,
        type=item.type,
        slug=PurePosixPath(path).name,
        repo_name=repo_name,
        repo_root=repo_root,
        branch=branch,
        worktree=worktrees_dir / repo_name / branch,
        parent_branch=parent_branch,
        parent_worktree=parent_worktree,
        owner_path=owner.path if owner is not None else None,
    )


def placement_chain(
    item: WorkItem,
    items: Mapping[str, WorkItem],
    *,
    repo_name: str | None,
    repo_root: Path | None,
    working_checkout: Path | None,
    worktrees_dir: Path,
    default_base: str,
) -> tuple[Placement, ...]:
    """Compute enclosing owners outermost first, then the item, in one repository."""
    chain: list[Placement] = []
    current: WorkItem | None = item
    seen: set[str] = set()
    while current is not None and current.path not in seen:
        seen.add(current.path)
        placement = placement_for(
            current,
            items,
            repo_name=repo_name,
            repo_root=repo_root,
            working_checkout=working_checkout,
            worktrees_dir=worktrees_dir,
            default_base=default_base,
        )
        if placement is None:
            return ()
        chain.append(placement)
        current = enclosing_owner(current, items)
    return tuple(reversed(chain))


@dataclass(frozen=True, slots=True)
class EffectivePlacement:
    repo_name: str
    worktree: str
    branch: str
    start_sha: str | None
    source: Literal["legacy", "computed"]


@dataclass(frozen=True, slots=True)
class LegacyRow:
    """One stored legacy placement; `repo_key=None` is the scalar pair (the item's own repository)."""

    repo_key: str | None
    worktree: str
    branch: str
    start_sha: str | None


def legacy_rows(item: WorkItem) -> tuple[LegacyRow, ...]:
    """The item's stored legacy placements: the scalar pair first, then `repo_stamps` by key."""
    legacy = item.legacy_placement
    if legacy is None:
        return ()
    rows: list[LegacyRow] = []
    if legacy.worktree or legacy.branch:
        rows.append(LegacyRow(None, legacy.worktree or "", legacy.branch or "", item.start_sha))
    rows.extend(
        LegacyRow(name, stamp.worktree, stamp.branch, stamp.start_sha) for name, stamp in sorted(legacy.repos.items())
    )
    return tuple(rows)


def stamp_row(item: WorkItem, repo_key: str | None) -> LegacyRow | None:
    """The item's legacy row for *repo_key* (`None` is the scalar pair), if it has one."""
    return next((row for row in legacy_rows(item) if row.repo_key == repo_key), None)


def stamped_worktrees(item: WorkItem) -> tuple[str, ...]:
    """Every non-empty worktree the item's legacy stamps name."""
    return tuple(row.worktree for row in legacy_rows(item) if row.worktree)


def effective_placement(
    item: WorkItem,
    items: Mapping[str, WorkItem],
    *,
    repo: ItemRepo,
    own_repo_name: str | None,
    worktrees_dir: Path,
    default_base: str,
) -> EffectivePlacement | None:
    """Where *item* lives in *repo*: a legacy stamp when one exists, else the computed placement."""
    legacy = item.legacy_placement
    if legacy is not None and repo.name is not None:
        stamp = legacy.repos.get(repo.name)
        if stamp is not None:
            return EffectivePlacement(
                repo.name, stamp.worktree, stamp.branch, stamp.start_sha or item.start_sha, "legacy"
            )
        if repo.name == own_repo_name and legacy.worktree and legacy.branch:
            return EffectivePlacement(repo.name, legacy.worktree, legacy.branch, item.start_sha, "legacy")
    if repo.name is None or repo.path is None:
        return None
    placement = placement_for(
        item,
        items,
        repo_name=repo.name,
        repo_root=repo.path,
        working_checkout=repo.path,
        worktrees_dir=worktrees_dir,
        default_base=default_base,
    )
    if placement is None:
        return None
    return EffectivePlacement(
        repo.name,
        str(placement.worktree),
        placement.branch,
        item.start_sha if repo.name == own_repo_name else None,
        "computed",
    )


@dataclass(frozen=True, slots=True)
class PlacementEvent:
    repo_root: Path
    worktree: Path
    parent_worktree: Path
    slug: str
    worktrees_dir: Path


class PlacementNotifier(Protocol):
    def placed(self, event: PlacementEvent) -> str | None: ...


class NullNotifier:
    def placed(self, event: PlacementEvent) -> str | None:
        return "orca: not configured"

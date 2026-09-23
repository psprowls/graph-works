"""Direct-child rollups and iterative hierarchy traversal."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from work_tracker_okf._selection import path_index
from work_tracker_okf.dependencies import DependencyEdge, entry_phase, resolve_facts, unmet
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import PARENT_TYPES, TERMINAL_STATUSES

PICK_ORDER: dict[str, int] = {"in-progress": 0, "accepted": 1, "open": 2}


@dataclass(frozen=True, slots=True)
class ChildRollup:
    total: int
    terminal: int
    open_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DescendResult:
    path: tuple[str, ...]
    leaf: str | None
    blocked_at: str | None = None
    reason: str | None = None


def _direct_children(items: Sequence[WorkItem], parent_path: str) -> tuple[WorkItem, ...]:
    index = path_index(items)
    parent = index.get(parent_path)
    if parent is None:
        return ()
    return tuple(index[path] for path in (*parent.active_child_paths, *parent.archived_child_paths) if path in index)


def child_rollup(items: Sequence[WorkItem], parent_path: str) -> ChildRollup:
    children = _direct_children(items, parent_path)
    terminal = sum(item.work_status in TERMINAL_STATUSES for item in children)
    open_paths = tuple(sorted(item.path for item in children if item.work_status not in TERMINAL_STATUSES))
    return ChildRollup(len(children), terminal, open_paths)


def active_nonterminal_descendants(items: Sequence[WorkItem], parent_path: str) -> tuple[str, ...]:
    """Every active nonterminal descendant of *parent_path*, at any depth."""
    index = path_index(items)
    parent = index.get(parent_path)
    if parent is None:
        return ()
    found: list[str] = []
    pending = list(reversed(parent.active_child_paths))
    seen = {parent_path}
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        item = index.get(path)
        if item is None or item.archived:
            continue
        if item.work_status not in TERMINAL_STATUSES:
            found.append(item.path)
        pending.extend(reversed(item.active_child_paths))
    return tuple(sorted(found))


def nearest_parent(items: Sequence[WorkItem], path: str) -> str | None:
    """Nearest Release, Epic, or Feature containing (or equal to) *path*."""
    from work_tracker_okf.vocabulary import PARENT_TYPES

    index = path_index(items)
    seen: set[str] = set()
    current: str | None = path
    while current is not None and current not in seen:
        item = index.get(current)
        if item is None:
            return None
        if item.type in PARENT_TYPES:
            return item.path
        seen.add(current)
        current = item.parent_path
    return None


def decision_owner(items: Sequence[WorkItem], path: str) -> str | None:
    """Who owns *path*'s decision ledger (D-001): the nearest Release, Epic or
    Feature containing it, or -- when there is none -- the item itself, so a
    lone Bug, TechDebt, TestGap or Spike can record decisions and holds.
    `None` only for an unknown path."""
    owner = nearest_parent(items, path)
    if owner is not None:
        return owner
    return path if path in path_index(items) else None


def archive_held_by_ancestor(items: Sequence[WorkItem], item: WorkItem) -> bool:
    """Whether *item* is a child the archive policy holds in place.

    `True` exactly when *item*'s nearest **non-archived** ancestor exists and is
    **not** terminal -- a resolved child of an open epic, in other words.

    **A work item is archived only as a root; children ride along, in place.**
    Sweeping such a child mid-epic moves its path out from under a parent page
    that still links to it at `children/<name>.md`, while the epic is being
    assembled, for a distinction ("this child was archived separately") that
    nothing needs.

    Archived ancestors are skipped rather than consulted: an archived page is
    frozen, and being contained by one says nothing about whether the live tree
    above is still open. An ancestor the projection does not know does not hold
    anything either -- an unresolvable path is not evidence of a live parent.

    Written once and shared by `archive._default_targets`, `archive.plan_archive`
    and `_rules.state.terminal`, so the lint cannot disagree with the planner
    about what is archivable -- the same discipline `okf_io.migrate()` follows
    in keeping reader, validator and writer on one set.
    """
    from work_tracker_okf.vocabulary import TERMINAL_STATUSES

    index = path_index(items)
    for path in reversed(item.ancestor_paths):
        ancestor = index.get(path)
        if ancestor is None or ancestor.archived:
            continue
        return ancestor.work_status not in TERMINAL_STATUSES
    return False


def declared_repo(item: WorkItem, items_by_path: Mapping[str, WorkItem]) -> tuple[str | None, str | None]:
    """`(repo, setter_path)`: the nearest `repo:` over *item*, then its physical ancestors.

    Nearest wins: the item itself, then `reversed(item.ancestor_paths)`
    (which is root-first). Names only -- mapping a name to a path is
    `graph_works_core.workspace.repos`'s job, since this band never reads
    `workspace.yaml`. A malformed `repo:` projects as `None` and is walked
    past like an absent one.
    """
    for path in (item.path, *reversed(item.ancestor_paths)):
        holder = item if path == item.path else items_by_path.get(path)
        if holder is not None and holder.repo is not None:
            return holder.repo, holder.path
    return None, None


def unknown_depends_on(items: Sequence[WorkItem], edges: Sequence[DependencyEdge]) -> dict[str, None]:
    known = path_index(items)
    return {edge.path: None for edge in edges if edge.path not in known}


def child_gated(items: Sequence[WorkItem], item: WorkItem) -> bool:
    """Gated iff `advance()`'s children-open guard would refuse. One authority."""
    if not active_nonterminal_descendants(items, item.path):
        return False
    return item.type in PARENT_TYPES and item.phase in {"execute", "finish"}


def nearest_epic(items: Sequence[WorkItem], path: str) -> str | None:
    index = path_index(items)
    seen: set[str] = set()
    current: str | None = path
    while current is not None and current not in seen:
        item = index.get(current)
        if item is None:
            return None
        if item.type == "Epic":
            return item.path
        seen.add(current)
        current = item.parent_path
    return None


def _dependency_blocked(items: Sequence[WorkItem], child: WorkItem) -> bool:
    phase = child.phase or entry_phase(child.type, child.effort)
    return phase is not None and bool(
        unmet(child.dependency_edges, resolve_facts(items, child.dependency_edges), phase)
    )


def descend(items: Sequence[WorkItem], path: str) -> DescendResult:
    index = path_index(items)
    node = index.get(path)
    if node is None:
        return DescendResult((path,), None, path, f"unknown path {path!r}")
    walked = [path]
    visited = {path}
    while True:
        children = _direct_children(items, node.path)
        if not child_gated(items, node):
            return DescendResult(tuple(walked), node.path)
        candidates = [
            child
            for child in children
            if not child.archived
            and not _dependency_blocked(items, child)
            and (
                child.work_status in PICK_ORDER
                or (child.work_status in TERMINAL_STATUSES and active_nonterminal_descendants(items, child.path))
            )
        ]
        if not candidates:
            return DescendResult(
                tuple(walked),
                None,
                node.path,
                "no dep-ready child: open children are blocked on dependencies or not dispatchable",
            )
        candidates.sort(
            key=lambda child: (PICK_ORDER.get(child.work_status, len(PICK_ORDER)), child.opened, child.path)
        )
        chosen = candidates[0]
        if chosen.path in visited:
            return DescendResult(
                tuple(walked), None, node.path, "parent cycle detected: " + " -> ".join([*walked, chosen.path])
            )
        walked.append(chosen.path)
        visited.add(chosen.path)
        node = chosen


__all__ = [
    "PICK_ORDER",
    "ChildRollup",
    "DescendResult",
    "active_nonterminal_descendants",
    "archive_held_by_ancestor",
    "child_gated",
    "child_rollup",
    "decision_owner",
    "declared_repo",
    "descend",
    "nearest_epic",
    "nearest_parent",
    "unknown_depends_on",
]

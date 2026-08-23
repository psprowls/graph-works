"""Direct-child rollups and iterative hierarchy traversal."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from work_tracker_okf._selection import path_index
from work_tracker_okf.dependencies import DependencyEdge, entry_phase, resolve_facts, unmet
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

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


def unknown_depends_on(items: Sequence[WorkItem], edges: Sequence[DependencyEdge]) -> dict[str, None]:
    known = path_index(items)
    return {edge.path: None for edge in edges if edge.path not in known}


def child_gated_node(item: WorkItem, children: Sequence[WorkItem]) -> bool:
    if not any(child.work_status not in TERMINAL_STATUSES for child in children):
        return False
    if item.type in {"Release", "Epic"}:
        return item.phase == "execute"
    return item.type == "Feature" and item.phase in {"execute", "finish"}


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
        if not child_gated_node(node, children):
            return DescendResult(tuple(walked), node.path)
        candidates = [
            child
            for child in children
            if not child.archived and child.work_status in PICK_ORDER and not _dependency_blocked(items, child)
        ]
        if not candidates:
            return DescendResult(
                tuple(walked),
                None,
                node.path,
                "no dep-ready child: open children are blocked on dependencies or not dispatchable",
            )
        candidates.sort(key=lambda child: (PICK_ORDER[child.work_status], child.opened, child.path))
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
    "child_gated_node",
    "child_rollup",
    "descend",
    "nearest_epic",
    "nearest_parent",
    "unknown_depends_on",
]

"""Direct-child rollups and iterative hierarchy traversal."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from work_tracker_okf.dependencies import DependencyEdge, resolve_facts, unmet
from work_tracker_okf.items import WorkItem
from work_tracker_okf.pipeline import (
    PACKAGED_DEFINITION,
    PipelineDefinition,
    entry_stage,
    path_attributes,
    resolve_path,
)
from work_tracker_okf.pipeline import child_gated as _row_child_gated
from work_tracker_okf.snapshot import WorkSnapshot, as_snapshot
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
    return as_snapshot(items).child_items(parent_path)


def child_rollup(items: Sequence[WorkItem], parent_path: str) -> ChildRollup:
    snapshot = as_snapshot(items)

    def compute() -> ChildRollup:
        children = snapshot.child_items(parent_path)
        terminal = sum(item.work_status in TERMINAL_STATUSES for item in children)
        open_paths = tuple(sorted(item.path for item in children if item.work_status not in TERMINAL_STATUSES))
        return ChildRollup(len(children), terminal, open_paths)

    return snapshot.memo("child_rollup", parent_path, compute)


def active_nonterminal_descendants(items: Sequence[WorkItem], parent_path: str) -> tuple[str, ...]:
    """Every active nonterminal descendant of *parent_path*, at any depth."""
    snapshot = as_snapshot(items)

    def compute() -> tuple[str, ...]:
        index = snapshot.by_path
        parent = index.get(parent_path)
        if parent is None:
            return ()
        found: list[str] = []
        pending = list(reversed(parent.child_paths))
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
            pending.extend(reversed(item.child_paths))
        return tuple(sorted(found))

    return snapshot.memo("active_nonterminal_descendants", parent_path, compute)


def nearest_parent(items: Sequence[WorkItem], path: str) -> str | None:
    """Nearest Release, Epic, or Feature containing (or equal to) *path*."""
    from work_tracker_okf.vocabulary import PARENT_TYPES

    snapshot = as_snapshot(items)

    def compute() -> str | None:
        index = snapshot.by_path
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

    return snapshot.memo("nearest_parent", path, compute)


def decision_owner(items: Sequence[WorkItem], path: str) -> str | None:
    """Who owns *path*'s decision ledger (D-001): the nearest Release, Epic or
    Feature containing it, or -- when there is none -- the item itself, so a
    lone Bug, TechDebt, TestGap or Spike can record decisions and holds.
    `None` only for an unknown path."""
    snapshot = as_snapshot(items)
    owner = nearest_parent(snapshot, path)
    if owner is not None:
        return owner
    return path if path in snapshot.by_path else None


def sweep_eligible(items: Sequence[WorkItem], item: WorkItem) -> bool:
    """Whether the archive sweep selects *item*.

    **A work item is archived only as a top-level root; its subtree rides
    along, unchanged in shape.** So *item* must be top-level, not archived,
    terminal, and every descendant at any depth must be terminal too. A root
    with an open descendant is not an error, only not yet eligible -- the
    sweep skips it rather than refusing the whole run.

    Written once and shared by `archive._default_targets` and
    `_rules.state.terminal`, so the lint cannot disagree with the planner
    about what the sweep will move.

    Memoized per snapshot only when *item* is the snapshot's own item at its
    path; a caller-supplied variant is answered fresh, never from the memo.
    """
    snapshot = as_snapshot(items)
    if snapshot.by_path.get(item.path) is item:
        return snapshot.memo("sweep_eligible", item.path, lambda: _sweep_eligible(snapshot, item))
    return _sweep_eligible(snapshot, item)


def _sweep_eligible(snapshot: WorkSnapshot, item: WorkItem) -> bool:
    from work_tracker_okf.vocabulary import TERMINAL_STATUSES

    if item.ancestor_paths or item.parent_path is not None or item.archived:
        return False
    if item.work_status not in TERMINAL_STATUSES:
        return False
    index = snapshot.by_path
    pending = list(item.child_paths)
    seen = {item.path}
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        descendant = index.get(path)
        if descendant is None:
            continue
        if descendant.work_status not in TERMINAL_STATUSES:
            return False
        pending.extend(descendant.child_paths)
    return True


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
    known = as_snapshot(items).by_path
    return {edge.path: None for edge in edges if edge.path not in known}


def child_gated(items: Sequence[WorkItem], item: WorkItem) -> bool:
    """Gated iff `advance()`'s children-open guard would refuse. One authority."""
    if not active_nonterminal_descendants(items, item.path):
        return False
    return _row_child_gated(item.type, item.phase)


def nearest_epic(items: Sequence[WorkItem], path: str) -> str | None:
    snapshot = as_snapshot(items)

    def compute() -> str | None:
        index = snapshot.by_path
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

    return snapshot.memo("nearest_epic", path, compute)


def _dependency_blocked(items: Sequence[WorkItem], child: WorkItem, definition: PipelineDefinition) -> bool:
    attrs = path_attributes(
        type_=child.type,
        effort=child.effort,
        blast_radius=child.blast_radius,
        has_spec=child.has_design_artifact,
        has_plan=child.has_plan_artifact,
        spec_stale=False,
    )
    phase = child.phase or entry_stage(definition, attrs)
    stages = {stage for candidate in resolve_path(definition, attrs).candidates for stage in candidate.stages}
    return phase is not None and bool(
        unmet(child.dependency_edges, resolve_facts(items, child.dependency_edges), phase, stages=stages)
    )


def descend(
    items: Sequence[WorkItem], path: str, *, definition: PipelineDefinition = PACKAGED_DEFINITION
) -> DescendResult:
    snapshot = as_snapshot(items)
    index = snapshot.by_path
    node = index.get(path)
    if node is None:
        return DescendResult((path,), None, path, f"unknown path {path!r}")
    walked = [path]
    visited = {path}
    while True:
        children = _direct_children(snapshot, node.path)
        if not child_gated(snapshot, node):
            return DescendResult(tuple(walked), node.path)
        candidates = [
            child
            for child in children
            if not child.archived
            and not _dependency_blocked(snapshot, child, definition)
            and (
                child.work_status in PICK_ORDER
                or (child.work_status in TERMINAL_STATUSES and active_nonterminal_descendants(snapshot, child.path))
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
    "child_gated",
    "child_rollup",
    "decision_owner",
    "declared_repo",
    "descend",
    "nearest_epic",
    "nearest_parent",
    "sweep_eligible",
    "unknown_depends_on",
]

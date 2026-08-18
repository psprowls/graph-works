"""The hierarchy graph: rollups, the dependency gates, and the descend walk.

Ported from `work_io.hierarchy` over `WorkItem` instead of `dict`, with
`children_map` deleted (C3-G: `load_items` already derives it) and the two
dependency queries renamed after what they return (C3-H).

Every function takes the **whole** item set, archived included: a reference to
an archived item is valid, and an archived child still belongs to its parent's
rollup. `work-io` needed a second loader for that; one walk retires it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")

#: How deep `descend` walks before giving up. Catches a cycle the visited set
#: cannot -- one formed by items appearing and disappearing from candidacy.
WALK_DEPTH_CAP = 32

#: Descend candidates, best first. A mapping rather than a membership test:
#: `mitigated` is non-terminal, so it holds a gate open, but it is absent here
#: and is therefore never a descend target.
PICK_ORDER: dict[str, int] = {"in-progress": 0, "accepted": 1, "open": 2}


@dataclass(frozen=True, slots=True)
class ChildRollup:
    """A parent's children, counted. `open_slugs` is what the gate messages name."""

    total: int
    terminal: int
    open_slugs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DescendResult:
    """Where a `--descend` walk landed. `path` is inclusive of both ends."""

    path: tuple[str, ...]
    leaf: str | None
    blocked_at: str | None = None
    reason: str | None = None


def child_rollup(items: Sequence[WorkItem], parent_slug: str) -> ChildRollup:
    """Roll up the children of *parent_slug* -- items whose `parent` is it."""
    children = [item for item in items if item.parent == parent_slug]
    terminal = sum(1 for item in children if item.workflow_status in TERMINAL_STATUSES)
    open_slugs = tuple(sorted(item.slug for item in children if item.workflow_status not in TERMINAL_STATUSES))
    return ChildRollup(total=len(children), terminal=terminal, open_slugs=open_slugs)


def unmet_depends_on(items: Sequence[WorkItem], depends_on: Sequence[str]) -> tuple[str, ...]:
    """The subset of *depends_on* that is not yet terminal, in declared order.

    A slug matching no item is **unmet**, not ignored: a typo blocks the stage
    rather than silently letting it run.
    """
    by_slug = {item.slug: item for item in items}
    return tuple(
        slug for slug in depends_on if slug not in by_slug or by_slug[slug].workflow_status not in TERMINAL_STATUSES
    )


def unknown_depends_on(items: Sequence[WorkItem], depends_on: Sequence[str]) -> dict[str, str | None]:
    """Values naming no item at all, mapped to a same-title hint or `None`.

    The hint fires only on an unambiguous match: exactly one known slug equal
    to the value once its `YYYY-MM-DD-` prefix is stripped.
    """
    known = {item.slug for item in items}
    unknown: dict[str, str | None] = {}
    for value in depends_on:
        if value in known:
            continue
        matches = sorted(slug for slug in known if _DATE_PREFIX_RE.sub("", slug) == value)
        unknown[value] = matches[0] if len(matches) == 1 else None
    return unknown


def child_gated_node(item: WorkItem, children: Sequence[WorkItem]) -> bool:
    """Whether *item* is a node `descend` walks **through** rather than lands on.

    An `Epic` is gated only at `execute` and a `Feature` at `execute` or
    `finish`. An epic still at design or plan is its own actionable leaf: it
    dispatches to the decomposition stage, and the children gate has not
    engaged yet. That clause must agree with `workflow._epic_execute_gate` --
    otherwise `--descend` and `next` disagree about the same item.
    """
    if not any(child.workflow_status not in TERMINAL_STATUSES for child in children):
        return False
    if item.type == "Epic":
        return item.phase == "execute"
    return item.type == "Feature" and item.phase in ("execute", "finish")


def nearest_epic(items: Sequence[WorkItem], slug: str) -> str | None:
    """The nearest ancestor of *slug* whose `type` is `Epic`, or `None`.

    *slug* itself counts: an epic is its own nearest epic. Cycle-safe and
    bounded by `WALK_DEPTH_CAP`, the same cap `descend` walks this tree downward
    under — a `parent` chain that closes on itself is `graph.parent-cycle`'s
    finding, and this walk must return rather than diagnose it.

    It lands here rather than in either consumer because it has two:
    `_rules/decisions.citations` and the auto-drive shell. Writing it twice
    means two walks that can disagree about cycles and depth.
    """
    by_slug = {item.slug: item for item in items}
    seen: set[str] = set()
    current: str | None = slug
    for _ in range(WALK_DEPTH_CAP):
        if current is None or current in seen:
            return None
        item = by_slug.get(current)
        if item is None:
            return None
        if item.type == "Epic":
            return item.slug
        seen.add(current)
        current = item.parent
    return None


def descend(items: Sequence[WorkItem], slug: str) -> DescendResult:
    """The next actionable leaf at or below *slug*. Cycle-safe and depth-capped.

    At each level the candidates are children whose `workflow_status` is in
    `PICK_ORDER` and whose own `depends_on` are all terminal, ordered by that
    rank then `(opened, slug)`.
    """
    by_slug = {item.slug: item for item in items}
    node = by_slug.get(slug)
    if node is None:
        return DescendResult(path=(slug,), leaf=None, blocked_at=slug, reason=f"unknown slug {slug!r}")
    path = [slug]
    visited = {slug}
    while True:
        children = [item for item in items if item.parent == node.slug]
        if not child_gated_node(node, children):
            return DescendResult(path=tuple(path), leaf=node.slug)
        candidates = [
            child
            for child in children
            if child.workflow_status in PICK_ORDER and not unmet_depends_on(items, child.depends_on)
        ]
        if not candidates:
            return DescendResult(
                path=tuple(path),
                leaf=None,
                blocked_at=node.slug,
                reason="no dep-ready child: open children are blocked on dependencies or not dispatchable",
            )
        candidates.sort(key=lambda child: (PICK_ORDER[child.workflow_status], child.opened, child.slug))
        chosen = candidates[0]
        if chosen.slug in visited:
            return DescendResult(
                path=tuple(path),
                leaf=None,
                blocked_at=node.slug,
                reason="parent cycle detected: " + " -> ".join([*path, chosen.slug]),
            )
        if len(path) >= WALK_DEPTH_CAP:
            return DescendResult(
                path=tuple(path),
                leaf=None,
                blocked_at=node.slug,
                reason=f"descend depth cap ({WALK_DEPTH_CAP}) reached: " + " -> ".join([*path, chosen.slug]),
            )
        path.append(chosen.slug)
        visited.add(chosen.slug)
        node = chosen


__all__ = [
    "PICK_ORDER",
    "WALK_DEPTH_CAP",
    "ChildRollup",
    "DescendResult",
    "child_gated_node",
    "child_rollup",
    "descend",
    "nearest_epic",
    "unknown_depends_on",
    "unmet_depends_on",
]

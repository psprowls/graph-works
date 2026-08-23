"""Plan direct-descendant indexes while preserving human-authored prose."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from work_tracker_okf.items import WorkItem
from work_tracker_okf.paths import child_lane, parse_item_path
from work_tracker_okf.vocabulary import PARENT_TYPES

GENERATED_START = "<!-- graph-works:work-items:start -->"
GENERATED_END = "<!-- graph-works:work-items:end -->"


@dataclass(frozen=True, slots=True)
class LaneIndexPlan:
    lane: str
    path: Path
    before: str | None
    after: str
    entries: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return self.before != self.after


def render_entry(item: WorkItem) -> str:
    """Render one lane-local index entry."""
    phase = item.phase or "not started"
    return f"- [{item.type}: {item.title}]({item.basename}.md) — {item.work_status} · {phase}"


def _required_lanes(items: Sequence[WorkItem]) -> tuple[str, ...]:
    lanes = {"work", "work/_archive"}
    for item in items:
        if not item.archived and item.type in PARENT_TYPES:
            lanes.add(child_lane(item.path))
            lanes.add(child_lane(item.path, archived=True))
    return tuple(sorted(lanes))


def _direct_items(items: Sequence[WorkItem], lane: str) -> tuple[WorkItem, ...]:
    direct: list[WorkItem] = []
    for item in items:
        location = parse_item_path(item.path)
        if location is not None and location.lane == lane:
            direct.append(item)
    return tuple(sorted(direct, key=lambda item: item.basename))


def _render_region(entries: Sequence[str]) -> str:
    body = "\n".join(entries)
    if body:
        body = f"{body}\n"
    return f"{GENERATED_START}\n{body}{GENERATED_END}"


def reconcile_marked_index(before: str | None, entries: Sequence[str]) -> str:
    """Merge generated *entries* into already-repaired optional index text."""
    region = _render_region(entries)
    if before is None or not before:
        return f"{region}\n"
    start = before.find(GENERATED_START)
    end = before.find(GENERATED_END, start + len(GENERATED_START)) if start >= 0 else -1
    if start >= 0 and end >= 0:
        end += len(GENERATED_END)
        return f"{before[:start]}{region}{before[end:]}"
    prose = before.rstrip("\n")
    separator = "\n\n" if prose else ""
    return f"{prose}{separator}{region}\n"


def plan_indexes(
    root: Path,
    items: Sequence[WorkItem],
    *,
    lanes: Iterable[str] | None = None,
) -> tuple[LaneIndexPlan, ...]:
    """Plan marked regions for requested lanes or every required work lane."""
    selected = tuple(sorted(set(lanes))) if lanes is not None else _required_lanes(items)
    plans: list[LaneIndexPlan] = []
    for lane in selected:
        path = root / lane / "index.md"
        try:
            before = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            before = None
        direct = _direct_items(items, lane)
        entries = tuple(render_entry(item) for item in direct)
        plans.append(LaneIndexPlan(lane, path, before, reconcile_marked_index(before, entries), entries))
    return tuple(plans)


__all__ = [
    "GENERATED_END",
    "GENERATED_START",
    "LaneIndexPlan",
    "plan_indexes",
    "reconcile_marked_index",
    "render_entry",
]

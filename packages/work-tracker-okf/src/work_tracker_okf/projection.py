"""Read-only status and resume projections over path-keyed work items."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from work_tracker_okf.hierarchy import ChildRollup, child_rollup
from work_tracker_okf.items import WorkItem
from work_tracker_okf.paths import item_page
from work_tracker_okf.vocabulary import PARENT_TYPES, TERMINAL_STATUSES

NON_ACTIONABLE_STATUSES: frozenset[str] = TERMINAL_STATUSES | {"mitigated"}
MAX_ALTERNATIVES = 3


@dataclass(frozen=True, slots=True)
class Rollup:
    total: int
    by_work_status: Mapping[str, int]
    by_type: Mapping[str, int]
    by_phase: Mapping[str, int]
    children: Mapping[str, ChildRollup]


@dataclass(frozen=True, slots=True)
class ResumeItem:
    path: str
    title: str


@dataclass(frozen=True, slots=True)
class ResumeSelection:
    primary: ResumeItem
    alternatives: tuple[ResumeItem, ...]


def _counts(values: Iterable[str]) -> Mapping[str, int]:
    return MappingProxyType(dict(sorted(Counter(value for value in values if value).items())))


def rollup(items: Sequence[WorkItem]) -> Rollup:
    active = tuple(item for item in items if not item.archived)
    children: dict[str, ChildRollup] = {}
    for item in active:
        if item.type not in PARENT_TYPES:
            continue
        rolled = child_rollup(items, item.path)
        if item.type not in {"Release", "Epic"} and rolled.total == 0:
            continue
        children[item.path] = rolled
    return Rollup(
        total=len(active),
        by_work_status=_counts(item.work_status for item in active),
        by_type=_counts(item.type for item in active),
        by_phase=_counts(item.phase for item in active if item.phase is not None),
        children=MappingProxyType(children),
    )


def select_resume(items: Sequence[WorkItem]) -> ResumeSelection | None:
    actionable = [item for item in items if not item.archived and item.work_status not in NON_ACTIONABLE_STATUSES]
    if not actionable:
        return None
    actionable.sort(key=lambda item: item.path)
    actionable.sort(key=lambda item: item.updated, reverse=True)
    selected = tuple(ResumeItem(item.path, item.title) for item in actionable)
    return ResumeSelection(selected[0], selected[1 : 1 + MAX_ALTERNATIVES])


def resolve(root: Path, path: str) -> Path | None:
    candidate = item_page(path).path(root)
    return candidate if candidate.is_file() else None


__all__ = [
    "MAX_ALTERNATIVES",
    "NON_ACTIONABLE_STATUSES",
    "ResumeItem",
    "ResumeSelection",
    "Rollup",
    "resolve",
    "rollup",
    "select_resume",
]

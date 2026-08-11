"""The sidecar's data, minus the artifact.

`work-index.json` itself is tier 4's (E-H): the atomic write, the git stamp and
the staleness check are all harness concerns. What is here is what the sidecar
was *for* -- the counts a status display reads, the selection a session-start
hook reads, and the slug -> path lookup the cache existed to serve.

That last one is why the cache goes away: W-E's symmetric archive move means a
slug has exactly **two** candidate paths, so `resolve` is two stat calls rather
than a JSON artifact with a staleness check.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from work_tracker_okf.hierarchy import ChildRollup, child_rollup
from work_tracker_okf.items import ARCHIVE_DIR, WORK_DIR, WorkItem
from work_tracker_okf.vocabulary import PARENT_TYPES, TERMINAL_STATUSES

#: Terminal plus `mitigated`: the dispositions a human owns, which never
#: resume. The same set `route` refuses to dispatch.
NON_ACTIONABLE_STATUSES: frozenset[str] = TERMINAL_STATUSES | {"mitigated"}

MAX_ALTERNATIVES = 3


@dataclass(frozen=True, slots=True)
class Rollup:
    """Counts over the active set, plus child rollups over the whole set.

    The two halves reading different sets is deliberate and looks like a bug
    otherwise: an archive that grows without bound would swamp the numbers a
    human reads, while an archived child still belongs to its parent's gate.
    """

    total: int
    by_workflow_status: Mapping[str, int]
    by_type: Mapping[str, int]
    by_phase: Mapping[str, int]
    children: Mapping[str, ChildRollup]


@dataclass(frozen=True, slots=True)
class ResumeItem:
    slug: str
    title: str


@dataclass(frozen=True, slots=True)
class ResumeSelection:
    primary: ResumeItem
    alternatives: tuple[ResumeItem, ...]


def _counts(values: Iterable[str]) -> Mapping[str, int]:
    counter = Counter(value for value in values if value)
    return MappingProxyType(dict(sorted(counter.items())))


def rollup(items: Sequence[WorkItem]) -> Rollup:
    """The counts. `by_phase` is new -- it is what a pipeline status display
    actually wants, and `work-io` computed it nowhere."""
    active = [item for item in items if not item.archived]
    children: dict[str, ChildRollup] = {}
    for item in active:
        if item.type not in PARENT_TYPES:
            continue
        rolled = child_rollup(items, item.slug)
        if item.type != "Epic" and rolled.total == 0:
            continue
        children[item.slug] = rolled
    return Rollup(
        total=len(active),
        by_workflow_status=_counts(item.workflow_status for item in active),
        by_type=_counts(item.type for item in active),
        by_phase=_counts(item.phase for item in active if item.phase is not None),
        children=MappingProxyType(children),
    )


def select_resume(items: Sequence[WorkItem]) -> ResumeSelection | None:
    """The item to offer resuming, plus up to `MAX_ALTERNATIVES` others.

    Ordering is `updated` descending then `slug` ascending -- deterministic,
    which `work-io`'s mtime ordering was not. The cost is real: ties within a
    day now break alphabetically rather than by recency. Nothing here reads a
    filesystem timestamp.
    """
    actionable = [item for item in items if not item.archived and item.workflow_status not in NON_ACTIONABLE_STATUSES]
    if not actionable:
        return None
    # Two stable passes: slug ascending, then the descending recency key on
    # top -- ties keep the slug-ascending order.
    actionable.sort(key=lambda item: item.slug)
    actionable.sort(key=lambda item: item.updated, reverse=True)
    return ResumeSelection(
        primary=_resume_item(actionable[0]),
        alternatives=tuple(_resume_item(item) for item in actionable[1 : 1 + MAX_ALTERNATIVES]),
    )


def _resume_item(item: WorkItem) -> ResumeItem:
    return ResumeItem(slug=item.slug, title=item.title)


def resolve(root: Path, slug: str) -> Path | None:
    """The page for *slug* under *root*: active first, then archived.

    The only filesystem touch in this module -- two stats, and the reason
    `work-index.json`'s slug -> path map is not ported.
    """
    for directory in (WORK_DIR, ARCHIVE_DIR):
        candidate = root / directory / f"{slug}.md"
        if candidate.is_file():
            return candidate
    return None


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

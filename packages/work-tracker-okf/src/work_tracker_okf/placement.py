"""Where a dispatched stage runs: the pure placement planner (D-006).

`plan_placement` decides whether an observed `worktree`/`branch` pair may be
written onto an item; `apply_placement` writes an accepted plan into a
`Document`. Neither reads git, Orca, the clock or the filesystem -- the caller
supplies the observation and `today=`, and `graph-works-core` serializes the
write with `gw work advance` under the decision owner's lock.

A placement plan never carries a routing transition. Recording where a stage
runs is a fact about a dispatch, not a stage completion, which is why this is
not an `advance` flag: `advance` always applies the next transition.

Entitlement follows the stamp's meaning. The orchestration **root** is
recorded at every phase -- its stamp is the anchor every descendant resolves
against. A **descendant** is recorded only at `execute` and `finish`: a
`design` or `plan` stage writes only into the vault, and pinning it to the
shared epic worktree would place its later code stages there too.

The phase guard compares the dispatched phase with the item's recorded phase,
or -- for a never-entered item -- the phase its routing entry transition
opens. It cannot tell two attempts of the same phase apart; binding an
observation to the current attempt is the coordinator's job.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import PurePath
from typing import Literal, get_args

from okf_io import Document

from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import EFFORTS, PHASES, TERMINAL_STATUSES, TYPES, WORK_STATUSES
from work_tracker_okf.workflow import route, state_for

PlacementRefusal = Literal[
    "unknown-path",
    "unknown-root",
    "outside-root",
    "invalid-item",
    "invalid-phase",
    "invalid-pair",
    "read-only-descendant",
    "terminal",
    "entry-unprovable",
    "phase-mismatch",
]

PLACEMENT_REFUSALS: frozenset[str] = frozenset(get_args(PlacementRefusal))

#: The phases whose stages commit, and so the only ones a descendant records at.
CODE_PHASES: frozenset[str] = frozenset({"execute", "finish"})

#: Every phase a stage can be dispatched at.
_DISPATCH_PHASES: frozenset[str] = PHASES - {"done"}


@dataclass(frozen=True, slots=True)
class PlacementPlan:
    """One placement decision. `changes` is empty for a refusal and for an
    identical pair. `apply_placement` rejects refusals and leaves an identical
    pair untouched."""

    path: str
    root: str
    expected_phase: str
    current_phase: str | None
    before: tuple[str | None, str | None]
    after: tuple[str, str]
    changes: tuple[tuple[str, object], ...]
    refusal: PlacementRefusal | None
    detail: str

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def plan_placement(
    items: Sequence[WorkItem],
    path: str,
    *,
    root: str,
    phase: str,
    worktree: str,
    branch: str,
    today: date,
) -> PlacementPlan:
    """Plan recording (*worktree*, *branch*) on *path* for a *phase* dispatch
    of the subtree rooted at *root*. Mutates nothing, reads no clock."""
    index = {item.path: item for item in items}
    item = index.get(path)
    before = (item.worktree, item.branch) if item is not None else (None, None)
    after = (worktree, branch)

    def refused(reason: PlacementRefusal, detail: str, current: str | None = None) -> PlacementPlan:
        return PlacementPlan(path, root, phase, current, before, after, (), reason, detail)

    if item is None:
        return refused("unknown-path", f"unknown work item {path!r}")
    if root not in index:
        return refused("unknown-root", f"unknown orchestration root {root!r}", item.phase)
    if path != root and root not in item.ancestor_paths:
        return refused("outside-root", f"{path} is not {root} or one of its descendants", item.phase)
    for candidate in (item, index[root]):
        problem = _item_problem(candidate)
        if problem is not None:
            return refused("invalid-item", problem, item.phase)
    if phase not in _DISPATCH_PHASES:
        return refused("invalid-phase", f"{phase!r} is not a dispatchable phase", item.phase)
    problem = _pair_problem(worktree, branch)
    if problem is not None:
        return refused("invalid-pair", problem, item.phase)
    if path != root and phase not in CODE_PHASES:
        return refused(
            "read-only-descendant",
            f"{path} is a descendant of {root}; a {phase} stage writes no code and records no placement",
            item.phase,
        )
    if item.work_status in TERMINAL_STATUSES or item.work_status == "mitigated" or item.phase == "done":
        return refused(
            "terminal", f"{path} is {item.work_status} at phase {item.phase!r}; nothing is dispatched", item.phase
        )
    current = item.phase if item.phase is not None else _entry_phase(items, item)
    if current is None:
        return refused("entry-unprovable", f"{path} has no phase and its routing entry cannot be proved")
    if current != phase:
        return refused(
            "phase-mismatch",
            f"{path} is at phase {current!r}, not the dispatched {phase!r}; inspect before recording",
            current,
        )
    changes: list[tuple[str, object]] = [
        (key, value)
        for key, old, value in (("worktree", before[0], worktree), ("branch", before[1], branch))
        if old != value
    ]
    if changes and item.updated != today.isoformat():
        # A `date`, not an ISO string: ruamel would quote a string that re-parses as a date.
        changes.append(("updated", today))
    return PlacementPlan(path, root, phase, current, before, after, tuple(changes), None, "")


def apply_placement(document: Document, plan: PlacementPlan) -> None:
    """Write *plan* into *document*. Nothing else on the page is touched."""
    assert plan.refusal is None, f"refused placement ({plan.refusal}) must not be applied"
    for key, value in plan.changes:
        document.set(key, value)


def _item_problem(item: WorkItem) -> str | None:
    """Validate placement-relevant vocabulary without requiring a transition.

    An absent/null phase or effort is valid; a malformed value erased by the
    tolerant projection is not. Routing alone proves a missing phase's entry.
    Holds and dependency gates do not invalidate a recorded phase.
    """
    if item.invalid_optional_fields:
        fields = ", ".join(item.invalid_optional_fields)
        return f"{item.path} has invalid {fields}; expected nonempty text or null; repair it first"
    for field, value, allowed in (
        ("type", item.type, TYPES),
        ("work_status", item.work_status, WORK_STATUSES),
        ("phase", item.phase, PHASES),
        ("effort", item.effort, EFFORTS),
    ):
        if value is None and field in {"phase", "effort"}:
            continue
        if value not in allowed:
            return f"{item.path} has invalid {field} {value!r}; repair it first"
    return None


def _entry_phase(items: Sequence[WorkItem], item: WorkItem) -> str | None:
    """The phase a never-entered item's entry transition opens, or `None`.

    `hold=` is deliberately not supplied: an open hold blocks transitions,
    not a factual placement record. Missing effort, dependency blockers or an
    invalid entry state all answer `None` -- never a default of `design`.
    """
    state = state_for(items, item.path)
    if state is None:  # pragma: no cover -- `item` was drawn from `items`
        return None
    result = route(state)
    if result.blockers or result.dispatch is None or result.on_dispatch is None:
        return None
    return result.on_dispatch.phase


def _pair_problem(worktree: str, branch: str) -> str | None:
    if not worktree or not branch:
        return "both --worktree and --branch are required and nonempty"
    for label, value in (("worktree", worktree), ("branch", branch)):
        if value != value.strip() or "\n" in value or "\r" in value:
            return f"{label} {value!r} carries surrounding whitespace or a line break"
    if not PurePath(worktree).is_absolute():
        return f"worktree {worktree!r} is not an absolute path on this host"
    if branch.startswith("refs/"):
        return f"branch {branch!r} is a ref; strip `refs/heads/` before recording"
    if branch == "HEAD":
        return "a detached HEAD has no branch to record"
    return None


__all__ = [
    "CODE_PHASES",
    "PLACEMENT_REFUSALS",
    "PlacementPlan",
    "PlacementRefusal",
    "apply_placement",
    "plan_placement",
]

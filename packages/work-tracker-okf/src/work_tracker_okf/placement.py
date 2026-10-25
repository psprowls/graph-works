"""The pure planner for the one stamp that survives computed placement.

Computed placement (the placement engine, `gw work place`) made every
`worktree`/`branch`/`repo_stamps` stamp redundant, and workspace writes land on
the primary checkout, so no workspace stamp is recorded either. One pure
planner remains, and it reads no git, Orca, clock or filesystem:
`plan_baseline` / `apply_baseline` decide and write the scalar `start_sha` the
placement engine's `start` mode records.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal, get_args

from okf_io import Document

from work_tracker_okf.items import WorkItem, is_commit_oid
from work_tracker_okf.pipeline import DONE, EXECUTE, code_phases
from work_tracker_okf.vocabulary import EFFORTS, PHASES, TERMINAL_STATUSES, TYPES, WORK_STATUSES

#: The phases whose stages commit.
CODE_PHASES: frozenset[str] = code_phases()


BaselineRefusal = Literal[
    "unknown-path",
    "invalid-item",
    "terminal",
    "not-execute",
    "invalid-baseline",
    "baseline-conflict",
    "no-repo",
    "outside-repository",
    "git-unavailable",
    "wrong-checkout",
    "already-started",
]
BASELINE_REFUSALS: frozenset[str] = frozenset(get_args(BaselineRefusal))


@dataclass(frozen=True, slots=True)
class BaselinePlan:
    """Recording the scalar execute baseline for an attended execute stage.
    The last five refusals are produced one band up, by the git-reading shell."""

    path: str
    before: str | None
    after: str | None
    changes: tuple[tuple[str, object], ...]
    refusal: BaselineRefusal | None
    detail: str

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def plan_baseline(
    items: Sequence[WorkItem], path: str, *, observed_head: str, head_descends_from_recorded: bool, today: date
) -> BaselinePlan:
    """Plan writing *observed_head* as *path*'s scalar `start_sha` before execute work starts.

    Absent: written. Equal: no-op. A recorded baseline *observed_head* descends
    from is kept -- the stage is already under way, and its range starts there.
    Anything else is `baseline-conflict`: two unrelated starting points. Reads no git.
    """
    item = next((candidate for candidate in items if candidate.path == path), None)
    before = item.start_sha if item is not None else None

    def refused(reason: BaselineRefusal, detail: str) -> BaselinePlan:
        return BaselinePlan(path, before, before, (), reason, detail)

    if item is None:
        return refused("unknown-path", f"unknown work item {path!r}")
    problem = _item_problem(item)
    if problem is not None:
        return refused("invalid-item", problem)
    if "start_sha" in item.invalid_optional_fields:
        return refused("invalid-item", f"{path} has a malformed start_sha; repair it first")
    if item.work_status in TERMINAL_STATUSES or item.work_status == "mitigated" or item.phase == DONE:
        return refused("terminal", f"{path} is {item.work_status} at phase {item.phase!r}")
    if item.phase != EXECUTE:
        return refused("not-execute", f"{path} is at phase {item.phase!r}; a baseline is recorded as execute starts")
    if not is_commit_oid(observed_head):
        return refused("invalid-baseline", f"observed HEAD {observed_head!r} is not a full commit object ID")
    if before is None:
        changes: list[tuple[str, object]] = [("start_sha", observed_head)]
        if item.updated != today.isoformat():
            changes.append(("updated", today))
        return BaselinePlan(path, None, observed_head, tuple(changes), None, "")
    if before == observed_head or head_descends_from_recorded:
        return BaselinePlan(path, before, before, (), None, "")
    return refused(
        "baseline-conflict",
        f"{path} records start_sha {before}, and HEAD {observed_head} does not descend from it; "
        "inspect where this stage's work started before recording",
    )


def apply_baseline(document: Document, plan: BaselinePlan) -> None:
    assert plan.refusal is None, f"refused baseline ({plan.refusal}) must not be applied"
    for key, value in plan.changes:
        document.set(key, value)


def _item_problem(item: WorkItem) -> str | None:
    """Validate placement-relevant vocabulary without requiring a transition.

    An absent/null phase or effort is valid; a malformed value erased by the
    tolerant projection is not. Routing alone proves a missing phase's entry.
    Holds and dependency gates do not invalidate a recorded phase.
    """
    lossy = tuple(field for field in item.invalid_optional_fields if field in {"phase", "effort"})
    if lossy:
        fields = ", ".join(lossy)
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


__all__ = [
    "BASELINE_REFUSALS",
    "CODE_PHASES",
    "BaselinePlan",
    "BaselineRefusal",
    "apply_baseline",
    "plan_baseline",
]

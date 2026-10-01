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
recorded at every phase, except an Epic/Release root, which is recorded only
at execute and finish. A **descendant**
records code placement only at `execute` and `finish`. These stamps select
later code checkouts; a design/plan reader's dedicated detached checkout must
never replace them, even for a root reader.

The phase guard compares the dispatched phase with the item's recorded phase,
or -- for a never-entered item -- the phase its routing entry transition
opens. It cannot tell two attempts of the same phase apart; binding an
observation to the current attempt is the coordinator's job.

Readers at design and plan are observed through a detached checkout receipt,
not stamped onto the work item. A reader plan carries no document changes.
"""

from __future__ import annotations

import re
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import PurePath
from typing import Literal, get_args

from okf_io import Document

from work_tracker_okf.items import COMMIT_OID as _OID
from work_tracker_okf.items import WorkItem, is_commit_oid
from work_tracker_okf.pipeline import (
    DECOMPOSING_TYPES,
    DONE,
    EXECUTE,
    PACKAGED_DEFINITION,
    PipelineDefinition,
    code_phases,
    dispatch_phases,
    read_only_phases,
)
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
    "read-only-owner",
    "terminal",
    "entry-unprovable",
    "phase-mismatch",
    "invalid-baseline",
    "baseline-conflict",
    "baseline-missing",
]

PLACEMENT_REFUSALS: frozenset[str] = frozenset(get_args(PlacementRefusal))

#: The phases whose stages commit, and so the only ones a descendant records at.
CODE_PHASES: frozenset[str] = code_phases()

#: Every phase a stage can be dispatched at.
_DISPATCH_PHASES: frozenset[str] = dispatch_phases()

ReaderRefusal = Literal[
    "unknown-path",
    "unknown-root",
    "outside-root",
    "invalid-item",
    "invalid-phase",
    "invalid-observation",
    "code-phase",
    "terminal",
    "entry-unprovable",
    "phase-mismatch",
]

READER_REFUSALS: frozenset[str] = frozenset(get_args(ReaderRefusal))
READER_PHASES: frozenset[str] = read_only_phases()
_ATTEMPT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


@dataclass(frozen=True, slots=True)
class ReaderObservation:
    task_id: str
    dispatch_id: str
    dispatch_key: str
    repo: str
    worktree: str
    start_sha: str


@dataclass(frozen=True, slots=True)
class ReaderReceiptPlan:
    path: str
    root: str
    expected_phase: str
    current_phase: str | None
    observation: ReaderObservation
    refusal: ReaderRefusal | None
    detail: str


def plan_reader_receipt(
    items: Sequence[WorkItem],
    path: str,
    *,
    root: str,
    phase: str,
    observation: ReaderObservation,
    definition: PipelineDefinition = PACKAGED_DEFINITION,
) -> ReaderReceiptPlan:
    """Check a design/plan reader observation without reading or writing state."""
    index = {item.path: item for item in items}
    item = index.get(path)

    def refused(reason: ReaderRefusal, detail: str, current: str | None = None) -> ReaderReceiptPlan:
        return ReaderReceiptPlan(path, root, phase, current, observation, reason, detail)

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
    problem = _observation_problem(observation)
    if problem is not None:
        return refused("invalid-observation", problem, item.phase)
    if phase not in READER_PHASES:
        return refused("code-phase", f"a {phase} stage writes code and has no reader receipt", item.phase)
    if item.work_status in TERMINAL_STATUSES or item.work_status == "mitigated" or item.phase == DONE:
        return refused(
            "terminal", f"{path} is {item.work_status} at phase {item.phase!r}; nothing is dispatched", item.phase
        )
    current = item.phase if item.phase is not None else _entry_phase(items, item, definition)
    if current is None:
        return refused("entry-unprovable", f"{path} has no phase and its routing entry cannot be proved")
    if current != phase:
        return refused(
            "phase-mismatch",
            f"{path} is at phase {current!r}, not the dispatched {phase!r}; inspect before recording",
            current,
        )
    return ReaderReceiptPlan(path, root, phase, current, observation, None, "")


def _observation_problem(o: ReaderObservation) -> str | None:
    for label, value in (
        ("task_id", o.task_id),
        ("dispatch_id", o.dispatch_id),
        ("dispatch_key", o.dispatch_key),
        ("repo", o.repo),
        ("worktree", o.worktree),
    ):
        if not value or value != value.strip() or "\n" in value or "\r" in value:
            return f"{label} {value!r} is blank or carries surrounding whitespace or a line break"
    if not _ATTEMPT.fullmatch(o.dispatch_id) or ".." in o.dispatch_id:
        return f"dispatch_id {o.dispatch_id!r} is not a plain attempt identifier"
    if not PurePath(o.worktree).is_absolute():
        return f"worktree {o.worktree!r} is not an absolute path on this host"
    if not _OID.fullmatch(o.start_sha):
        return f"start_sha {o.start_sha!r} is not a full lowercase commit object ID"
    return None


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
    repo: str | None = None
    """`None` is the scalar pair; a name is `repo_stamps[name]`, passed only
    for a repository other than the item's own."""
    start_before: str | None = None
    start_after: str | None = None

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
    repo: str | None = None,
    start_sha: str | None = None,
    require_start_sha: bool = False,
    definition: PipelineDefinition = PACKAGED_DEFINITION,
) -> PlacementPlan:
    """Plan recording (*worktree*, *branch*) on *path* for a *phase* dispatch
    of the subtree rooted at *root*. Mutates nothing, reads no clock.

    The pair travels with its execute baseline (`start_sha`). With the pair
    unchanged and no *start_sha* given, a recorded baseline is kept; with the
    pair changed and none given, the recorded baseline is dropped (a changed
    placement never retains the prior SHA). A given *start_sha* equal to the
    recorded one is a no-op, one filling an absent baseline is written, and one
    differing from a recorded baseline on an unchanged pair refuses
    `baseline-conflict`. A malformed one refuses `invalid-baseline`.
    *require_start_sha* refuses `baseline-missing` when the resulting stamp
    would have no baseline.
    """
    index = {item.path: item for item in items}
    item = index.get(path)
    if item is None or repo is None:
        before = (item.worktree, item.branch) if item is not None else (None, None)
        start_before = item.start_sha if item is not None else None
    else:
        stamp = item.repo_stamps.get(repo)
        before = (stamp.worktree, stamp.branch) if stamp is not None else (None, None)
        start_before = stamp.start_sha if stamp is not None else None
    after = (worktree, branch)

    def refused(reason: PlacementRefusal, detail: str, current: str | None = None) -> PlacementPlan:
        return PlacementPlan(
            path, root, phase, current, before, after, (), reason, detail, repo, start_before, start_before
        )

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
    if repo is not None and "repo_stamps" in item.invalid_optional_fields:
        return refused(
            "invalid-item",
            f"{path} has a malformed repo_stamps; repair it before recording a placement for {repo!r}",
            item.phase,
        )
    if phase not in _DISPATCH_PHASES:
        return refused("invalid-phase", f"{phase!r} is not a dispatchable phase", item.phase)
    problem = _pair_problem(worktree, branch, repo)
    if problem is not None:
        return refused("invalid-pair", problem, item.phase)
    if start_sha is not None and not is_commit_oid(start_sha):
        return refused(
            "invalid-baseline", f"start_sha {start_sha!r} is not a full lowercase commit object ID", item.phase
        )
    if repo is None and "start_sha" in item.invalid_optional_fields:
        return refused(
            "invalid-item", f"{path} has a malformed start_sha; repair it before recording a placement", item.phase
        )
    if path != root and phase not in CODE_PHASES:
        return refused(
            "read-only-descendant",
            f"{path} is a descendant of {root}; a {phase} stage writes no code and records no placement",
            item.phase,
        )
    if item.work_status in TERMINAL_STATUSES or item.work_status == "mitigated" or item.phase == DONE:
        return refused(
            "terminal", f"{path} is {item.work_status} at phase {item.phase!r}; nothing is dispatched", item.phase
        )
    if path == root and item.type in DECOMPOSING_TYPES and phase not in CODE_PHASES:
        return refused(
            "read-only-owner",
            f"{path} is an {item.type} reading at {phase}; its anchors are recorded by execute-time preparation",
            item.phase,
        )
    current = item.phase if item.phase is not None else _entry_phase(items, item, definition)
    if current is None:
        return refused("entry-unprovable", f"{path} has no phase and its routing entry cannot be proved")
    if current != phase:
        return refused(
            "phase-mismatch",
            f"{path} is at phase {current!r}, not the dispatched {phase!r}; inspect before recording",
            current,
        )
    pair_changed = before != after
    if start_sha is None:
        start_after = None if pair_changed else start_before
    elif not pair_changed and start_before is not None and start_before != start_sha:
        return refused(
            "baseline-conflict",
            f"{path} already records start_sha {start_before} for {worktree}; the observed {start_sha} differs "
            "-- inspect before recording",
            current,
        )
    else:
        start_after = start_sha
    if require_start_sha and start_after is None:
        return refused(
            "baseline-missing",
            f"{path} has no recorded start_sha for {worktree} and none was proved; a code placement needs its baseline",
            current,
        )
    changes: list[tuple[str, object]] = [
        (key, value)
        for key, old, value in (("worktree", before[0], worktree), ("branch", before[1], branch))
        if old != value
    ]
    if start_after != start_before:
        changes.append(("start_sha", start_after))
    if changes and item.updated != today.isoformat():
        # A `date`, not an ISO string: ruamel would quote a string that re-parses as a date.
        changes.append(("updated", today))
    return PlacementPlan(
        path, root, phase, current, before, after, tuple(changes), None, "", repo, start_before, start_after
    )


def apply_placement(document: Document, plan: PlacementPlan) -> None:
    """Write *plan* into *document*. Nothing else on the page is touched.

    A foreign-repository plan (`plan.repo` set) writes its pair under
    `repo_stamps[plan.repo]`, leaving the scalar pair and every other entry
    as they were; `updated` stays top-level either way.
    """
    assert plan.refusal is None, f"refused placement ({plan.refusal}) must not be applied"
    if plan.repo is None:
        for key, value in plan.changes:
            if value is None:
                document.delete(key)
            else:
                document.set(key, value)
        return
    stamp_keys = ("worktree", "branch", "start_sha")
    pair = {key: value for key, value in plan.changes if key in stamp_keys}
    if pair:
        stamps = document.fm_raw.get("repo_stamps")
        fresh: dict[str, str] = {"worktree": plan.after[0], "branch": plan.after[1]}
        if plan.start_after is not None:
            fresh["start_sha"] = plan.start_after
        if not isinstance(stamps, MutableMapping):
            document.set("repo_stamps", {plan.repo: fresh})
        else:
            entry = stamps.get(plan.repo)
            if isinstance(entry, MutableMapping):
                for key, value in pair.items():
                    if value is None:
                        entry.pop(key, None)
                    else:
                        entry[key] = value
            else:
                stamps[plan.repo] = fresh
            document.mark_dirty()
    for key, value in plan.changes:
        if key == "updated":
            document.set(key, value)


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
]
BASELINE_REFUSALS: frozenset[str] = frozenset(get_args(BaselineRefusal))


@dataclass(frozen=True, slots=True)
class BaselinePlan:
    """Recording the scalar execute baseline for an attended execute stage.
    The last three refusals are produced one band up, by the git-reading shell."""

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


def _entry_phase(items: Sequence[WorkItem], item: WorkItem, definition: PipelineDefinition) -> str | None:
    """The phase a never-entered item's entry transition opens, or `None`.

    `hold=` is deliberately not supplied: an open hold blocks transitions,
    not a factual placement record. Missing effort, dependency blockers or an
    invalid entry state all answer `None` -- never a default of `design`.
    """
    # Passes no `stale_spec`: this package runs no git, so the plan-stage
    # reconcile reroute is only visible through `gw work next`.
    state = state_for(items, item.path)
    if state is None:  # pragma: no cover -- `item` was drawn from `items`
        return None
    result = route(state, definition=definition)
    if result.blockers or result.dispatch is None or result.on_dispatch is None:
        return None
    return result.on_dispatch.phase


def _pair_problem(worktree: str, branch: str, repo: str | None = None) -> str | None:
    if repo is not None and (not repo or repo != repo.strip() or "\n" in repo or "\r" in repo):
        return f"repo {repo!r} is blank or carries surrounding whitespace or a line break"
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
    "BASELINE_REFUSALS",
    "CODE_PHASES",
    "PLACEMENT_REFUSALS",
    "READER_PHASES",
    "READER_REFUSALS",
    "BaselinePlan",
    "BaselineRefusal",
    "PlacementPlan",
    "PlacementRefusal",
    "ReaderObservation",
    "ReaderReceiptPlan",
    "ReaderRefusal",
    "apply_baseline",
    "apply_placement",
    "plan_baseline",
    "plan_placement",
    "plan_reader_receipt",
]

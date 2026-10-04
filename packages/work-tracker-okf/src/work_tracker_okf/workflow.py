"""The routing table: a work item's state -> what to dispatch and what changes.

Pure. `route` interprets a `PipelineDefinition` (stage table plus path rules)
for a `RouteState`: what stage to dispatch, the transitions the stage table
offers, and typed blockers. Mapping a stage to a skill is the dispatch rules'
job, one band up.

`Transition`s are frozen data, never control flow: `advance` reads them, and
`advance` is the only mutation point.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.dependencies import (
    DependencyEdge,
    DependencyFact,
    DependencyIssue,
    describe,
    resolve_facts,
    unmet,
    validate_dependencies,
)
from work_tracker_okf.hierarchy import ChildRollup, active_nonterminal_descendants, child_rollup
from work_tracker_okf.items import WorkItem
from work_tracker_okf.pipeline import (
    DECOMPOSING_TYPES,
    DESIGN,
    DONE,
    EXECUTE,
    FINISH,
    PACKAGED_DEFINITION,
    PLAN,
    AttributeValue,
    PathCandidate,
    PathResolution,
    PipelineDefinition,
    Stage,
    StageRow,
    path_attributes,
    phase_ordinals,
    resolve_path,
    row,
)
from work_tracker_okf.snapshot import as_snapshot
from work_tracker_okf.vocabulary import (
    EFFORTS,
    PARENT_TYPES,
    PHASES,
    TERMINAL_STATUSES,
    TYPES,
    WORK_STATUSES,
)


@dataclass(frozen=True, slots=True)
class RouteState:
    """The facts the table reads. Narrow on purpose (C3-B): it is what
    keeps the table testable as a table, and some of the fields are properties
    of a graph (or of a decisions ledger) rather than of one item.

    `hold` is resolved by the caller from a ledger read; this module stays pure
    and never reads a decisions ledger or the filesystem itself.

    `stale_spec` names siblings landed since the spec baseline whose `affects` overlap;
    resolved by the caller from git, like `hold`.

    `has_branch` is branch *ownership* only, from the item's scalar `branch:`
    or `repo_stamps`: a stamped Epic or Release owns the integration branch its descendants merged
    into, so it finishes like any other branch (D-002). Never a name, never a
    Git read."""

    type: str
    work_status: str
    phase: str | None = None
    effort: str | None = None
    blast_radius: str | None = None
    has_plan_doc: bool = False
    has_spec_doc: bool = False
    hold: HoldFact | None = None
    stale_spec: tuple[str, ...] = ()
    dependency_edges: tuple[DependencyEdge, ...] = ()
    dependency_facts: tuple[DependencyFact, ...] = ()
    dependency_issues: tuple[DependencyIssue, ...] = ()
    child_rollup: ChildRollup | None = None
    open_descendants: tuple[str, ...] = ()
    has_branch: bool = False


@dataclass(frozen=True, slots=True)
class Transition:
    """One frontmatter mutation. `None` fields are left unchanged.

    `stamp_baseline` is an unresolved request like `stamp_source`: core
    resolves it to `spec_baseline` in the same write.
    """

    phase: str | None = None
    work_status: str | None = None
    document_status: str | None = None
    requires: tuple[str, ...] = ()
    sync_plan_table: bool = False
    stamp_source: str | None = None
    stamp_baseline: bool = False


def hold_blocker(hold: HoldFact) -> str:
    """The one hold blocker string.

    Public because reconciling-spec quotes it and a test pins the quote to
    this function (design §9).
    """
    return (
        f"open decision {hold.decision_id} ({hold.shape}) holds {hold.path}: answer via "
        f"`gw work decision answer {hold.path} {hold.decision_id} --answer ...`, then re-run"
    )


def hold_reason(hold: HoldFact, current_phase: str | None) -> str:
    return (
        f"open decision {hold.decision_id} holds this item ({hold.shape} at {hold.phase or current_phase or 'entry'})"
    )


def _validate(state: RouteState) -> list[str]:
    blockers = []
    if state.type not in TYPES:
        blockers.append(f"type {state.type!r} not in {sorted(TYPES)}")
    if state.work_status not in WORK_STATUSES:
        blockers.append(f"work_status {state.work_status!r} not in {sorted(WORK_STATUSES)}")
    if state.phase is not None and state.phase not in PHASES:
        blockers.append(f"phase {state.phase!r} not in {sorted(PHASES)}")
    if state.effort is not None and state.effort not in EFFORTS:
        blockers.append(f"effort {state.effort!r} not in {sorted(EFFORTS)}; re-size the item")
    blockers.extend(f"depends_on[{issue.index}] {issue.code}: {issue.detail}" for issue in state.dependency_issues)
    return blockers


def state_for(
    items: Sequence[WorkItem],
    path: str,
    *,
    effort: str | None = None,
    hold: HoldFact | None = None,
    stale_spec: tuple[str, ...] = (),
) -> RouteState | None:
    """The `RouteState` for *path*, or `None` when no item has that path.

    Two rules a rewrite drops by not knowing about them:

    - **The childless-feature rule.** A rollup is attached only for a
      `PARENT_TYPES` item, and then only for a Release/Epic or a non-empty rollup.
      A childless `Feature` carries `None`, so no gate fires; a Release or Epic
      keeps its zero-count rollup, because its no-children blocker uses it.
    - **Archived items participate.** Dependency facts and the rollup are
      computed over the whole projection. One walk retires `work-io`'s second
      loader and the bug it fixed -- a resolved-and-archived dependency reading
      as unmet.

    `effort=` overrides the item's own value: it is what lets a caller resolve
    the design-complete fork in the same call that supplies the size.

    `stale_spec=` is resolved by the caller from git. Default `()` is correct
    for callers that have no git context.

    `hold=` is resolved by the caller, never by this module: a decisions ledger
    read is IO, and this function stays pure. Default `None` is correct for
    callers that have no ledger to consult.
    """
    snapshot = as_snapshot(items)
    # Caller-authored duplicate paths intentionally use the last item; loaders yield unique paths.
    item = snapshot.by_path.get(path)
    if item is None:
        return None
    rollup: ChildRollup | None = None
    open_descendants: tuple[str, ...] = ()
    if item.type in PARENT_TYPES:
        rollup = child_rollup(snapshot, path)
        open_descendants = active_nonterminal_descendants(snapshot, path)
        if item.type not in DECOMPOSING_TYPES and rollup.total == 0:
            rollup = None
    structural_issues = tuple(
        issue
        for issue in validate_dependencies(item.dependency_edges, parent_path=item.parent_path, self_path=item.path)
        if issue.code in {"targets-parent", "targets-self"}
    )
    return RouteState(
        type=item.type,
        work_status=item.work_status,
        phase=item.phase,
        effort=effort or item.effort,
        blast_radius=item.blast_radius,
        has_plan_doc=item.has_plan_artifact,
        has_spec_doc=item.has_design_artifact,
        hold=hold,
        stale_spec=stale_spec,
        dependency_edges=item.dependency_edges,
        dependency_facts=resolve_facts(snapshot, item.dependency_edges),
        dependency_issues=(*item.dependency_issues, *structural_issues),
        child_rollup=rollup,
        open_descendants=open_descendants,
        has_branch=bool(item.branch or item.repo_stamps),
    )


RETURN_TO_EXECUTE = Transition(phase="execute", work_status="in-progress")


BlockerKind = Literal[
    "invalid",
    "done",
    "terminal",
    "hold",
    "invalid-entry",
    "dependencies",
    "effort-required",
    "attribute-required",
    "no-children",
    "waiting-on-children",
    "phase-off-path",
    "path-invalid",
    "finish-incomplete",
]


@dataclass(frozen=True, slots=True)
class Blocker:
    """Why nothing dispatches. `message` is the prose callers have always shown;
    `kind` is what a caller branches on. `finish-incomplete` is appended one band
    up by `stage_advance`, never produced here."""

    kind: BlockerKind
    message: str


@dataclass(frozen=True, slots=True)
class Dispatch:
    """What to run. The stage alone: the dispatch rules choose the skill from
    the item's attributes (epic D-008)."""

    stage: Stage


@dataclass(frozen=True, slots=True)
class RouteResult:
    """What to dispatch, the offered transitions, and typed blockers.

    `on_return` offers the backwards finish-to-execute transition; `repair`
    also identifies a transition that fixes an off-path phase.
    `path_candidates` lists paths that agree on the current stage but disagree
    on the next one. Completion then has `phase=None` and requires sizing.
    """

    dispatch: Dispatch | None
    reason: str
    on_dispatch: Transition | None = None
    on_complete: Transition | None = None
    blockers: tuple[Blocker, ...] = ()
    on_return: Transition | None = None
    repair: Transition | None = None
    path_candidates: tuple[PathCandidate, ...] = ()


def blocker_messages(result: RouteResult) -> tuple[str, ...]:
    return tuple(blocker.message for blocker in result.blockers)


def _blocked(kind: BlockerKind, reason: str, message: str) -> RouteResult:
    return RouteResult(dispatch=None, reason=reason, blockers=(Blocker(kind, message),))


def route_attributes(state: RouteState) -> Mapping[str, AttributeValue]:
    return path_attributes(
        type_=state.type,
        effort=state.effort,
        blast_radius=state.blast_radius,
        has_spec=state.has_spec_doc,
        has_plan=state.has_plan_doc,
        spec_stale=bool(state.stale_spec),
    )


def route(state: RouteState, *, definition: PipelineDefinition = PACKAGED_DEFINITION) -> RouteResult:
    """The interpreter. Gate order is load-bearing and unchanged: validation,
    done, terminal, hold, entry guard; then the path, the phase on it, the
    dependency gate, the row's child gate, and the row's transitions."""
    problems = _validate(state)
    if problems:
        return RouteResult(
            dispatch=None, reason="invalid item", blockers=tuple(Blocker("invalid", p) for p in problems)
        )
    if state.phase == DONE:
        return _blocked("done", "pipeline complete", "phase=done: nothing to dispatch; archive once the item ages out")
    if state.work_status in TERMINAL_STATUSES or state.work_status == "mitigated":
        return _blocked(
            "terminal",
            "disposition is human-owned",
            f"work_status {state.work_status!r} never dispatches; set it to 'open' to re-enter the pipeline",
        )
    if state.hold is not None:
        return _blocked("hold", hold_reason(state.hold, state.phase), hold_blocker(state.hold))
    if state.phase is None and state.work_status != "open":
        return _blocked(
            "invalid-entry",
            "invalid entry",
            f"no phase and work_status {state.work_status!r}; set it to 'open' to enter at design, "
            "or hand-set phase (e.g. phase: execute for an accepted item with a plan) "
            "to adopt an in-flight item mid-pipeline",
        )
    resolution = resolve_path(definition, route_attributes(state))
    if not resolution.complete or not resolution.candidates:
        return _blocked(
            "path-invalid",
            "no path rule matches",
            f"no path rule matches {state.type}; add a catch-all path rule (match: {{}})",
        )
    for candidate in resolution.candidates:
        if state.type in DECOMPOSING_TYPES and PLAN not in candidate.stages:
            return _blocked(
                "path-invalid",
                "path cannot decompose",
                f"{state.type} path [{', '.join(candidate.stages)}] (rule {candidate.rule.name!r}) lacks plan; "
                "a path rule may not stop decomposition",
            )
    located = {candidate: _locate(candidate.stages, state.phase) for candidate in resolution.candidates}
    if len(set(located.values())) > 1:
        return _fork_blocker(state, resolution, located)
    stage, off_path = next(iter(located.values()))
    if off_path:
        first = resolution.candidates[0]
        return RouteResult(
            dispatch=None,
            reason=f"phase {state.phase} is not on this item's path",
            blockers=(
                Blocker(
                    "phase-off-path",
                    f"phase {state.phase!r} is not on this item's path [{', '.join(first.stages)}] "
                    f"(rule {first.rule.name!r}); repair moves it to {stage!r}",
                ),
            ),
            repair=Transition(phase=stage),
        )
    blocker = _dependency_blocker(
        state, stage, frozenset(s for candidate in resolution.candidates for s in candidate.stages)
    )
    if blocker is not None:
        return blocker
    nexts = {_after(candidate.stages, stage) for candidate in resolution.candidates}
    next_phase = next(iter(nexts)) if len(nexts) == 1 else None
    forked = resolution.candidates if len(nexts) > 1 else ()
    unset = resolution.unset if forked else ()
    return _emit(state, definition, row(stage), resolution.candidates[0].stages, next_phase, forked, unset)


def _locate(stages: tuple[Stage, ...], phase: str | None) -> tuple[Stage, bool]:
    """(stage to run now, whether the phase was off this path). An off-path
    phase locates at the first stage after it -- the repair target."""
    if phase is None:
        return stages[0], False
    if phase in stages:
        return next(stage for stage in stages if stage == phase), False
    ordinals = phase_ordinals()
    return next(stage for stage in stages if ordinals[stage] > ordinals[phase]), True


def _after(stages: tuple[Stage, ...], stage: Stage) -> str:
    index = stages.index(stage)
    return stages[index + 1] if index + 1 < len(stages) else "done"


def _fork_blocker(
    state: RouteState, resolution: PathResolution, located: Mapping[PathCandidate, tuple[Stage, bool]]
) -> RouteResult:
    """Candidates disagree on the stage to run now (D-005, amended)."""
    groups: dict[str, list[str]] = {}
    for candidate in resolution.candidates:
        values = [candidate.assignment[a] for a in resolution.unset]
        label = (
            values[0]
            if len(values) == 1
            else ", ".join(f"{a}={v}" for a, v in zip(resolution.unset, values, strict=True))
        )
        groups.setdefault(located[candidate][0], []).append(label)
    routes = " or ".join(f"{stage} ({'/'.join(labels)})" for stage, labels in groups.items())
    if resolution.unset == ("effort",):
        kind: BlockerKind = "effort-required"
        message = f"effort required: {state.type} routes to {routes}; size the item and advance with the effort"
    else:
        names = ", ".join(resolution.unset)
        kind = "effort-required" if "effort" in resolution.unset else "attribute-required"
        message = f"{names} required: {state.type} routes to {routes}; set {names} on the item and re-run"
    return RouteResult(
        dispatch=None,
        reason=(
            "test-gap entry forks on effort"
            if state.type == "TestGap" and state.phase is None and resolution.unset == ("effort",)
            else f"{state.type.lower()} path forks on {', '.join(resolution.unset)}"
        ),
        blockers=(Blocker(kind, message),),
    )


def _dependency_blocker(state: RouteState, phase: str, stages: frozenset[str]) -> RouteResult | None:
    pairs = unmet(state.dependency_edges, state.dependency_facts, phase, stages=stages)
    if not pairs:
        return None
    fragments = "\n  ".join(describe(edge, fact) for edge, fact in pairs)
    return _blocked(
        "dependencies", f"blocked on dependencies ({phase})", f"blocked on dependencies for {phase}:\n  {fragments}"
    )


def _children_requires(state: RouteState, stage_row: StageRow) -> tuple[str, ...]:
    """A child-gated type that is not decomposing (Feature) acts, but cannot
    complete while descendants are open."""
    if state.type in stage_row.child_gated_types and state.type not in DECOMPOSING_TYPES and state.open_descendants:
        return ("children-terminal",)
    return ()


def _emit(
    state: RouteState,
    definition: PipelineDefinition,
    stage_row: StageRow,
    stages: tuple[Stage, ...],
    next_phase: str | None,
    forked: tuple[PathCandidate, ...],
    unset: tuple[str, ...],
) -> RouteResult:
    stage = stage_row.stage
    decomposing = state.type in DECOMPOSING_TYPES
    on_return = None
    if stage_row.return_target is not None and stage_row.return_target in stages:
        on_return = Transition(phase=stage_row.return_target, work_status=stage_row.return_status)
    if stage == EXECUTE and decomposing and state.type in stage_row.child_gated_types:
        return _parent_execute_gate(state, next_phase)
    if stage == FINISH and decomposing and state.type in stage_row.child_gated_types:
        if state.open_descendants:
            return RouteResult(
                dispatch=None,
                reason=f"{state.type.lower()} at finish stage: reopened by later children",
                blockers=(
                    Blocker(
                        "waiting-on-children",
                        f"waiting on children filed after finish: {', '.join(state.open_descendants)}",
                    ),
                ),
                on_return=on_return,
                repair=on_return,
            )
        if not state.has_branch:
            return RouteResult(
                dispatch=None,
                reason=f"{state.type.lower()} at finish stage",
                on_complete=Transition(phase="done", work_status=stage_row.on_complete.work_status),
                on_return=on_return,
            )
    effect = stage_row.on_complete
    requires = list(effect.requires)
    if stage == DESIGN and state.effort is None:
        requires.append("effort")
    requires.extend(a for a in unset if a not in requires)
    requires.extend(_children_requires(state, stage_row))
    work_status = effect.work_status
    if work_status is None and next_phase == EXECUTE and stage != PLAN:
        # A path that skips plan still enters execute as `accepted`, the state
        # orchestrate reads as "no execute stage has started yet".
        work_status = "accepted"
    on_complete = Transition(
        phase=next_phase,
        work_status=work_status,
        document_status=effect.document_status,
        requires=tuple(requires),
        sync_plan_table=effect.sync_plan_table and not decomposing,
        stamp_source=effect.stamp_source,
        stamp_baseline=effect.stamp_baseline,
    )
    reason = _reason(state, stage)
    if stage == PLAN and state.phase is not None and state.stale_spec:
        on_complete = Transition(phase="plan", stamp_baseline=True)
        reason = f"spec baseline stale: {', '.join(state.stale_spec)} landed since with overlapping affects"
    return RouteResult(
        dispatch=Dispatch(stage),
        reason=reason,
        on_dispatch=_on_dispatch(state, stage_row),
        on_complete=on_complete,
        on_return=on_return,
        path_candidates=forked,
    )


def _on_dispatch(state: RouteState, stage_row: StageRow) -> Transition | None:
    """Entry sets the phase (and `document_status=stable` when design is
    skipped); the row's `on_enter` applies unless its status already holds."""
    enter = stage_row.on_enter
    applies = enter is not None and state.work_status != enter.work_status
    if state.phase is not None:
        if not applies or enter is None:
            return None
        return Transition(work_status=enter.work_status, requires=enter.requires)
    return Transition(
        phase=stage_row.stage,
        work_status=enter.work_status if applies and enter is not None else None,
        document_status=None if stage_row.stage == DESIGN else "stable",
        requires=enter.requires if applies and enter is not None else (),
    )


def _parent_execute_gate(state: RouteState, next_phase: str | None) -> RouteResult:
    """Baseline `_parent_execute_gate`, with typed blockers and the next path stage."""
    rollup = state.child_rollup
    if rollup is None or rollup.total == 0:
        return _blocked(
            "no-children",
            f"{state.type.lower()} execute: no children",
            f"{state.type.lower()} has no children; run the plan stage to decompose it",
        )
    if state.open_descendants:
        return _blocked(
            "waiting-on-children",
            f"{state.type.lower()} execute: waiting on children",
            f"waiting on children: {rollup.terminal}/{rollup.total} terminal; "
            f"open: {', '.join(state.open_descendants)}",
        )
    return RouteResult(
        dispatch=None, reason=f"{state.type.lower()} children complete", on_complete=Transition(phase=next_phase)
    )


def _reason(state: RouteState, stage: str) -> str:
    if stage == DESIGN:
        entering = (
            "entering design with a pre-seeded spec: reconciling"
            if state.phase is None
            else "at design stage with an existing spec: reconciling"
        )
        plain = "entering the pipeline at design" if state.phase is None else "at design stage"
        return f"{state.type} {entering if state.has_spec_doc else plain}"
    if state.phase is None:
        return f"{state.type} with effort {state.effort}: enters at {stage}"
    if stage == EXECUTE:
        return (
            "execute stage with a written plan"
            if state.has_plan_doc
            else "execute stage via the test-driven path (no plan)"
        )
    return f"{state.type} at {stage} stage"


__all__ = [
    "Blocker",
    "BlockerKind",
    "Dispatch",
    "RouteResult",
    "RouteState",
    "Stage",
    "Transition",
    "blocker_messages",
    "hold_blocker",
    "hold_reason",
    "route",
    "route_attributes",
    "state_for",
]

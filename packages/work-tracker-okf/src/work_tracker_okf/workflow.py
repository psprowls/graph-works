"""The routing table: a work item's state -> what to dispatch and what changes.

Pure. `route` answers a `RouteState` with a `(stage, variant)` pair plus the
dispatch-time and completion-time transitions; mapping the seven pairs to
seven skill names is tier 4's job at this seam (E-C), which is why nothing
here names a skill.

`Transition`s are frozen data, never control flow: `advance` reads them, and
`advance` is the only mutation point.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from work_tracker_okf.dependencies import (
    DependencyEdge,
    DependencyFact,
    DependencyIssue,
    describe,
    entry_phase,
    resolve_facts,
    unmet,
    validate_dependencies,
)
from work_tracker_okf.hierarchy import ChildRollup, child_rollup
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import (
    BUG_LIKE_TYPES,
    DIAGNOSIS_TYPES,
    EFFORTS,
    PARENT_TYPES,
    PHASES,
    PLAN_SOURCE_ID,
    SMALL_EFFORTS,
    SPEC_SOURCE_ID,
    TERMINAL_STATUSES,
    TYPES,
    WORK_STATUSES,
)

#: The phase **reported** when the design-complete fork cannot be decided
#: without an effort value. Never written to frontmatter: it only ever appears
#: with `requires=("effort",)`, `advance` refuses it on its own besides, and it
#: is absent from `vocabulary.PHASES` so the schema would reject it anyway.
PLAN_OR_EXECUTE = "plan-or-execute"

Stage = Literal["design", "plan", "execute", "finish"]
Variant = Literal["exploration", "diagnosis", "reconcile", "decompose", "single", "planned", "unplanned", "branch"]


@dataclass(frozen=True, slots=True)
class RouteState:
    """The ten facts the table reads. Narrow on purpose (C3-B): it is what
    keeps the table testable as a table, and some of the fields are properties
    of a graph (or of a decisions ledger) rather than of one item.

    `has_open_decision` and `has_spec_doc` are both booleans a caller resolves
    and passes in -- this module stays pure and never reads a decisions ledger
    or the filesystem itself."""

    type: str
    work_status: str
    phase: str | None = None
    effort: str | None = None
    has_plan_doc: bool = False
    has_spec_doc: bool = False
    has_open_decision: bool = False
    dependency_edges: tuple[DependencyEdge, ...] = ()
    dependency_facts: tuple[DependencyFact, ...] = ()
    dependency_issues: tuple[DependencyIssue, ...] = ()
    child_rollup: ChildRollup | None = None


@dataclass(frozen=True, slots=True)
class Transition:
    """One frontmatter mutation. `None` fields are left unchanged."""

    phase: str | None = None
    work_status: str | None = None
    document_status: str | None = None
    requires: tuple[str, ...] = ()
    sync_plan_table: bool = False
    stamp_source: str | None = None


@dataclass(frozen=True, slots=True)
class Dispatch:
    """What to run. One field, not two, so `stage` and `variant` cannot disagree."""

    stage: Stage
    variant: Variant


@dataclass(frozen=True, slots=True)
class RouteResult:
    dispatch: Dispatch | None
    reason: str
    on_dispatch: Transition | None = None
    on_complete: Transition | None = None
    blockers: tuple[str, ...] = ()


def route(state: RouteState) -> RouteResult:
    """The table. Order is load-bearing: validation first so a malformed page
    reports *what* is malformed, and the dependency gate after the terminal
    checks so a resolved item blocked on a dep reports 'resolved'."""
    blockers = _validate(state)
    if blockers:
        return RouteResult(dispatch=None, reason="invalid item", blockers=tuple(blockers))
    if state.phase == "done":
        return RouteResult(
            dispatch=None,
            reason="pipeline complete",
            blockers=("phase=done: nothing to dispatch; archive once the item ages out",),
        )
    if state.work_status in TERMINAL_STATUSES or state.work_status == "mitigated":
        return RouteResult(
            dispatch=None,
            reason="disposition is human-owned",
            blockers=(
                f"work_status {state.work_status!r} never dispatches; set it to 'open' to re-enter the pipeline",
            ),
        )
    if state.phase is None:
        return _entry(state)
    branches: dict[str, Callable[[RouteState], RouteResult]] = {
        "design": _design,
        "plan": _plan,
        "execute": _execute,
        "finish": _finish,
    }
    return branches[state.phase](state)


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


def _dependency_blocker(state: RouteState, phase: str) -> RouteResult | None:
    pairs = unmet(state.dependency_edges, state.dependency_facts, phase)
    if not pairs:
        return None
    fragments = "\n  ".join(describe(edge, fact) for edge, fact in pairs)
    return RouteResult(
        dispatch=None,
        reason=f"blocked on dependencies ({phase})",
        blockers=(f"blocked on dependencies for {phase}:\n  {fragments}",),
    )


def _entry(state: RouteState) -> RouteResult:
    """First dispatch: no phase. Sets the entry phase via `on_dispatch`."""
    if state.work_status != "open":
        return RouteResult(
            dispatch=None,
            reason="invalid entry",
            blockers=(
                f"no phase and work_status {state.work_status!r}; set it to 'open' to enter at design, "
                "or hand-set phase (e.g. phase: execute for an accepted item with a plan) "
                "to adopt an in-flight item mid-pipeline",
            ),
        )
    # The gap is identified at filing time, so there is no design stage to
    # advance out of -- which is why the effort fork lands here instead,
    # and why both rows carry `document_status` (spec 3.3): otherwise the
    # item that skipped design keeps W-D's draft exemption for life.
    if state.type == "TestGap" and state.effort is None:
        return RouteResult(
            dispatch=None,
            reason="test-gap entry forks on effort",
            blockers=(
                "effort required: TestGap routes to execute (xtra-small/small) or plan "
                "(medium/large/xtra-large); size the item and advance with the effort",
            ),
        )
    phase = entry_phase(state.type, state.effort)
    if phase is not None:
        blocker = _dependency_blocker(state, phase)
        if blocker is not None:
            return blocker
    if state.type == "TestGap":
        if state.effort in SMALL_EFFORTS:
            return RouteResult(
                dispatch=Dispatch("execute", "unplanned"),
                reason=f"TestGap with effort {state.effort}: skip design and plan",
                on_dispatch=Transition(
                    phase="execute", work_status="in-progress", document_status="stable", requires=("owner",)
                ),
                on_complete=Transition(phase="finish"),
            )
        return RouteResult(
            dispatch=Dispatch("plan", "single"),
            reason=f"TestGap with effort {state.effort}: skip design, plan first",
            on_dispatch=Transition(phase="plan", document_status="stable"),
            on_complete=Transition(
                phase="execute", work_status="accepted", sync_plan_table=True, stamp_source=PLAN_SOURCE_ID
            ),
        )
    reason = (
        f"{state.type} entering design with a pre-seeded spec: reconciling"
        if state.has_spec_doc
        else f"{state.type} entering the pipeline at design"
    )
    return RouteResult(
        dispatch=Dispatch("design", _design_variant(state)),
        reason=reason,
        on_dispatch=Transition(phase="design"),
        on_complete=_design_complete(state),
    )


def _design_variant(state: RouteState) -> Variant:
    if state.has_spec_doc:
        return "reconcile"
    return "diagnosis" if state.type in DIAGNOSIS_TYPES else "exploration"


def _design_complete(state: RouteState) -> Transition:
    """The effort fork: small bug-like work skips planning.

    `Epic` is **not** bug-like and falls through to `plan` for every effort.
    Decomposition happens at plan and is mandatory; an epic sized small that
    skipped planning would reach execute with no children and immediately hit
    the epic gate's no-children blocker.
    """
    if state.type in BUG_LIKE_TYPES:
        if state.effort is None:
            return Transition(
                phase=PLAN_OR_EXECUTE,
                document_status="stable",
                requires=("effort",),
                stamp_source=SPEC_SOURCE_ID,
            )
        if state.effort in SMALL_EFFORTS:
            return Transition(phase="execute", document_status="stable", stamp_source=SPEC_SOURCE_ID)
    return Transition(phase="plan", document_status="stable", stamp_source=SPEC_SOURCE_ID)


def _design(state: RouteState) -> RouteResult:
    # Order matters: a held item (a contradiction filed as an open decision by
    # a previous reconciling-spec pass) must never fall through to another
    # dispatch -- that is the infinite-redispatch loop this gate exists to
    # stop. Checked before the spec-doc branch, deliberately.
    blocker = _dependency_blocker(state, "design")
    if blocker is not None:
        return blocker
    if state.has_open_decision:
        return RouteResult(
            dispatch=None,
            reason="design blocked: open decision needs a human answer",
            blockers=(
                "open decision(s) block re-dispatch: answer via "
                "`gw work decision answer <path> D-nnn --answer ...`, then re-run",
            ),
        )
    reason = (
        f"{state.type} at design stage with an existing spec: reconciling"
        if state.has_spec_doc
        else f"{state.type} at design stage"
    )
    return RouteResult(
        dispatch=Dispatch("design", _design_variant(state)),
        reason=reason,
        on_complete=_design_complete(state),
    )


def _plan(state: RouteState) -> RouteResult:
    blocker = _dependency_blocker(state, "plan")
    if blocker is not None:
        return blocker
    if state.type in {"Release", "Epic"}:
        # A release or epic decomposes into children and has no implementation row to
        # add, so no plan table to sync.
        return RouteResult(
            dispatch=Dispatch("plan", "decompose"),
            reason=f"{state.type} at plan stage",
            on_complete=Transition(phase="execute", work_status="accepted", stamp_source=PLAN_SOURCE_ID),
        )
    return RouteResult(
        dispatch=Dispatch("plan", "single"),
        reason=f"{state.type} at plan stage",
        on_complete=Transition(
            phase="execute", work_status="accepted", sync_plan_table=True, stamp_source=PLAN_SOURCE_ID
        ),
    )


def _parent_execute_gate(state: RouteState) -> RouteResult:
    """The Release/Epic gate blocks dispatch while child work remains."""
    rollup = state.child_rollup
    if rollup is None or rollup.total == 0:
        return RouteResult(
            dispatch=None,
            reason=f"{state.type.lower()} execute: no children",
            blockers=(f"{state.type.lower()} has no children; run the plan stage to decompose it",),
        )
    if rollup.terminal < rollup.total:
        return RouteResult(
            dispatch=None,
            reason=f"{state.type.lower()} execute: waiting on children",
            blockers=(
                f"waiting on children: {rollup.terminal}/{rollup.total} terminal; open: {', '.join(rollup.open_paths)}",
            ),
        )
    return RouteResult(
        dispatch=None,
        reason=f"{state.type.lower()} children complete",
        on_complete=Transition(phase="finish"),
    )


def _feature_children_requires(state: RouteState) -> tuple[str, ...]:
    """The feature gate: it rides `Transition.requires`, because a feature has
    work of its own to dispatch. It can act; it cannot finish."""
    rollup = state.child_rollup
    if state.type == "Feature" and rollup is not None and rollup.open_paths:
        return ("children-terminal",)
    return ()


def _execute(state: RouteState) -> RouteResult:
    blocker = _dependency_blocker(state, "execute")
    if blocker is not None:
        return blocker
    if state.type in {"Release", "Epic"}:
        return _parent_execute_gate(state)
    if state.has_plan_doc:
        variant: Variant = "planned"
        reason = "execute stage with a written plan"
    else:
        variant = "unplanned"
        reason = "execute stage via the test-driven path (no plan)"
    on_dispatch = None
    if state.work_status != "in-progress":
        on_dispatch = Transition(work_status="in-progress", requires=("owner",))
    return RouteResult(
        dispatch=Dispatch("execute", variant),
        reason=reason,
        on_dispatch=on_dispatch,
        on_complete=Transition(phase="finish", requires=_feature_children_requires(state)),
    )


def _finish(state: RouteState) -> RouteResult:
    blocker = _dependency_blocker(state, "finish")
    if blocker is not None:
        return blocker
    if state.type in {"Release", "Epic"}:
        # Releases and epics own no branch -- their descendants carry `resolved_in`.
        return RouteResult(
            dispatch=None,
            reason=f"{state.type.lower()} at finish stage",
            on_complete=Transition(phase="done", work_status="resolved"),
        )
    return RouteResult(
        dispatch=Dispatch("finish", "branch"),
        reason=f"{state.type} at finish stage",
        on_complete=Transition(
            phase="done",
            work_status="resolved",
            requires=("resolved_in", *_feature_children_requires(state)),
        ),
    )


def state_for(
    items: Sequence[WorkItem], path: str, *, effort: str | None = None, has_open_decision: bool = False
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

    `has_open_decision=` is resolved by the caller, never by this module: a
    decisions ledger read is IO, and this function stays pure. Default `False`
    is correct for every caller that has no ledger to consult (a lone item, or
    `advance`/`cli.next_stage`, neither of which currently resolves one).
    """
    item = next((candidate for candidate in items if candidate.path == path), None)
    if item is None:
        return None
    rollup: ChildRollup | None = None
    if item.type in PARENT_TYPES:
        rollup = child_rollup(items, path)
        if item.type not in {"Release", "Epic"} and rollup.total == 0:
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
        has_plan_doc=item.has_plan_artifact,
        has_spec_doc=item.has_design_artifact,
        has_open_decision=has_open_decision,
        dependency_edges=item.dependency_edges,
        dependency_facts=resolve_facts(items, item.dependency_edges),
        dependency_issues=(*item.dependency_issues, *structural_issues),
        child_rollup=rollup,
    )


__all__ = [
    "PLAN_OR_EXECUTE",
    "Dispatch",
    "RouteResult",
    "RouteState",
    "Stage",
    "Transition",
    "Variant",
    "route",
    "state_for",
]

"""route() frozen verbatim at 25f7d0f6 (epic/configurable-pipeline-path).

Parity oracle for test_route_parity.py; never edit the routing logic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from work_tracker_okf.dependencies import (
    describe,
    unmet,
)
from work_tracker_okf.vocabulary import (
    BUG_LIKE_TYPES,
    DIAGNOSIS_TYPES,
    EFFORTS,
    PHASES,
    PLAN_SOURCE_ID,
    SMALL_EFFORTS,
    SPEC_SOURCE_ID,
    TERMINAL_STATUSES,
    TYPES,
    WORK_STATUSES,
)
from work_tracker_okf.workflow import RouteState, Transition, hold_blocker, hold_reason

#: The phase **reported** when the design-complete fork cannot be decided
#: without an effort value. Never written to frontmatter: it only ever appears
#: with `requires=("effort",)`, `advance` refuses it on its own besides, and it
#: is absent from `vocabulary.PHASES` so the schema would reject it anyway.
LEGACY_PLAN_OR_EXECUTE = "plan-or-execute"


#: Which variants each stage can dispatch. `legacy_route()` pairs every `LegacyDispatch`
#: with a variant from its own stage's tuple; orchestrate uses the mapping to
#: classify a live key whose exact variant it cannot recover (a live key names
#: a phase, not a variant). `reconcile` is the one variant offered by two stages.


#: The one backwards transition the table offers: `finish` -> `execute`, with
#: the item put back in progress. Named once because both `_finish` arms
#: return it and a second literal is a second thing to keep in step.
RETURN_TO_EXECUTE = Transition(phase="execute", work_status="in-progress")


@dataclass(frozen=True, slots=True)
class LegacyDispatch:
    """What to run. One field, not two, so `stage` and `variant` cannot disagree."""

    stage: str
    variant: str


@dataclass(frozen=True, slots=True)
class LegacyRouteResult:
    """What to dispatch, and the three transitions the table offers.

    `on_return` is the only one that moves an item **backwards**, and it is a
    field of the table rather than a special case in `advance` because a phase
    change belongs where every other phase change is written. Only `_finish`
    sets it: an item that has already reached `finish` -- because it got there
    before the `execute -> finish` gate existed, because the relay put it on
    `hold`, or because a later stage-gate sends it back -- otherwise has no
    supported legacy_route home, and hand-editing frontmatter is not one.
    """

    dispatch: LegacyDispatch | None
    reason: str
    on_dispatch: Transition | None = None
    on_complete: Transition | None = None
    blockers: tuple[str, ...] = ()
    on_return: Transition | None = None
    repair: Transition | None = None


def legacy_route(state: RouteState) -> LegacyRouteResult:
    """The table. Order is load-bearing: validation first so a malformed page
    reports *what* is malformed, terminal checks next so a resolved item says
    so, then the hold gate before every phase-specific branch."""
    blockers = _validate(state)
    if blockers:
        return LegacyRouteResult(dispatch=None, reason="invalid item", blockers=tuple(blockers))
    if state.phase == "done":
        return LegacyRouteResult(
            dispatch=None,
            reason="pipeline complete",
            blockers=("phase=done: nothing to dispatch; archive once the item ages out",),
        )
    if state.work_status in TERMINAL_STATUSES or state.work_status == "mitigated":
        return LegacyRouteResult(
            dispatch=None,
            reason="disposition is human-owned",
            blockers=(
                f"work_status {state.work_status!r} never dispatches; set it to 'open' to re-enter the pipeline",
            ),
        )
    # Every phase, one place (D-002): after validation and the terminal
    # checks so a malformed or finished item still says so, and before any
    # branch so a held item gets no dispatch, no transition and no repair.
    # The literal `open decision` prefix is what `orchestrate._classify` keys on.
    if state.hold is not None:
        return LegacyRouteResult(
            dispatch=None,
            reason=hold_reason(state.hold, state.phase),
            blockers=(hold_blocker(state.hold),),
        )
    if state.phase is None:
        return _entry(state)
    branches: dict[str, Callable[[RouteState], LegacyRouteResult]] = {
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


def _dependency_blocker(state: RouteState, phase: str) -> LegacyRouteResult | None:
    pairs = unmet(state.dependency_edges, state.dependency_facts, phase)
    if not pairs:
        return None
    fragments = "\n  ".join(describe(edge, fact) for edge, fact in pairs)
    return LegacyRouteResult(
        dispatch=None,
        reason=f"blocked on dependencies ({phase})",
        blockers=(f"blocked on dependencies for {phase}:\n  {fragments}",),
    )


def _entry(state: RouteState) -> LegacyRouteResult:
    """First dispatch: no phase. Sets the entry phase via `on_dispatch`."""
    if state.work_status != "open":
        return LegacyRouteResult(
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
        return LegacyRouteResult(
            dispatch=None,
            reason="test-gap entry forks on effort",
            blockers=(
                "effort required: TestGap routes to execute (xtra-small/small) or plan "
                "(medium/large/xtra-large); size the item and advance with the effort",
            ),
        )
    phase = _legacy_entry_phase(state.type, state.effort)
    if phase is not None:
        blocker = _dependency_blocker(state, phase)
        if blocker is not None:
            return blocker
    if state.type == "TestGap":
        if state.effort in SMALL_EFFORTS:
            return LegacyRouteResult(
                dispatch=LegacyDispatch("execute", "unplanned"),
                reason=f"TestGap with effort {state.effort}: skip design and plan",
                on_dispatch=Transition(
                    phase="execute", work_status="in-progress", document_status="stable", requires=("owner",)
                ),
                on_complete=Transition(phase="finish"),
            )
        return LegacyRouteResult(
            dispatch=LegacyDispatch("plan", "single"),
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
    return LegacyRouteResult(
        dispatch=LegacyDispatch("design", _design_variant(state)),
        reason=reason,
        on_dispatch=Transition(phase="design"),
        on_complete=_design_complete(state),
    )


def _design_variant(state: RouteState) -> str:
    # Order matters: `reconcile` keeps precedence over the type check. An Epic
    # filed from a template has a spec to reconcile against, and reconciling one
    # beats re-designing it from a blank page for every type (D-002).
    if state.has_spec_doc:
        return "reconcile"
    if state.type in {"Release", "Epic"}:
        return "epic-design"
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
                phase=LEGACY_PLAN_OR_EXECUTE,
                document_status="stable",
                requires=("effort",),
                stamp_source=SPEC_SOURCE_ID,
                stamp_baseline=True,
            )
        if state.effort in SMALL_EFFORTS:
            return Transition(
                phase="execute", document_status="stable", stamp_source=SPEC_SOURCE_ID, stamp_baseline=True
            )
    return Transition(
        phase="plan",
        document_status="stable",
        stamp_source=SPEC_SOURCE_ID,
        stamp_baseline=True,
        requires=("effort",) if state.effort is None else (),
    )


def _design(state: RouteState) -> LegacyRouteResult:
    # Phase-specific dependency checks happen inside each branch.
    blocker = _dependency_blocker(state, "design")
    if blocker is not None:
        return blocker
    reason = (
        f"{state.type} at design stage with an existing spec: reconciling"
        if state.has_spec_doc
        else f"{state.type} at design stage"
    )
    return LegacyRouteResult(
        dispatch=LegacyDispatch("design", _design_variant(state)),
        reason=reason,
        on_complete=_design_complete(state),
    )


def _plan(state: RouteState) -> LegacyRouteResult:
    blocker = _dependency_blocker(state, "plan")
    if blocker is not None:
        return blocker
    if state.stale_spec:
        return LegacyRouteResult(
            dispatch=LegacyDispatch("plan", "reconcile"),
            reason=f"spec baseline stale: {', '.join(state.stale_spec)} landed since with overlapping affects",
            on_complete=Transition(phase="plan", stamp_baseline=True),
        )
    if state.type in {"Release", "Epic"}:
        # A release or epic decomposes into children and has no implementation row to
        # add, so no plan table to sync.
        return LegacyRouteResult(
            dispatch=LegacyDispatch("plan", "decompose"),
            reason=f"{state.type} at plan stage",
            on_complete=Transition(phase="execute", work_status="accepted", stamp_source=PLAN_SOURCE_ID),
        )
    return LegacyRouteResult(
        dispatch=LegacyDispatch("plan", "single"),
        reason=f"{state.type} at plan stage",
        on_complete=Transition(
            phase="execute", work_status="accepted", sync_plan_table=True, stamp_source=PLAN_SOURCE_ID
        ),
    )


def _parent_execute_gate(state: RouteState) -> LegacyRouteResult:
    """The Release/Epic gate blocks dispatch while child work remains.

    The decision reads `open_descendants` (any depth), not the direct-child
    rollup: a terminal direct child can still hold an open grandchild, and the
    rollup alone would call that satisfied. `child_rollup` stays only for the
    "no children" blocker and the `n/m terminal` counts in the message."""
    rollup = state.child_rollup
    if rollup is None or rollup.total == 0:
        return LegacyRouteResult(
            dispatch=None,
            reason=f"{state.type.lower()} execute: no children",
            blockers=(f"{state.type.lower()} has no children; run the plan stage to decompose it",),
        )
    if state.open_descendants:
        return LegacyRouteResult(
            dispatch=None,
            reason=f"{state.type.lower()} execute: waiting on children",
            blockers=(
                f"waiting on children: {rollup.terminal}/{rollup.total} terminal; "
                f"open: {', '.join(state.open_descendants)}",
            ),
        )
    return LegacyRouteResult(
        dispatch=None,
        reason=f"{state.type.lower()} children complete",
        on_complete=Transition(phase="finish"),
    )


def _feature_children_requires(state: RouteState) -> tuple[str, ...]:
    """The feature gate: it rides `Transition.requires`, because a feature has
    work of its own to dispatch. It can act; it cannot finish."""
    if state.type == "Feature" and state.open_descendants:
        return ("children-terminal",)
    return ()


def _execute(state: RouteState) -> LegacyRouteResult:
    blocker = _dependency_blocker(state, "execute")
    if blocker is not None:
        return blocker
    if state.type in {"Release", "Epic"}:
        return _parent_execute_gate(state)
    if state.has_plan_doc:
        variant: str = "planned"
        reason = "execute stage with a written plan"
    else:
        variant = "unplanned"
        reason = "execute stage via the test-driven path (no plan)"
    on_dispatch = None
    if state.work_status != "in-progress":
        on_dispatch = Transition(work_status="in-progress", requires=("owner",))
    return LegacyRouteResult(
        dispatch=LegacyDispatch("execute", variant),
        reason=reason,
        on_dispatch=on_dispatch,
        on_complete=Transition(phase="finish", requires=_feature_children_requires(state)),
    )


def _finish(state: RouteState) -> LegacyRouteResult:
    blocker = _dependency_blocker(state, "finish")
    if blocker is not None:
        return blocker
    if state.type in {"Release", "Epic"}:
        if state.open_descendants:
            # A child filed after this item reached `finish` reopens the gate:
            # `advance()`'s children-open guard would refuse `on_complete` here,
            # so plan the repair instead of a transition that will be refused.
            return LegacyRouteResult(
                dispatch=None,
                reason=f"{state.type.lower()} at finish stage: reopened by later children",
                blockers=(f"waiting on children filed after finish: {', '.join(state.open_descendants)}",),
                on_return=RETURN_TO_EXECUTE,
                repair=RETURN_TO_EXECUTE,
            )
        if not state.has_branch:
            # An unstamped (hand-driven) parent owns no branch -- its
            # descendants carry `resolved_in`.
            return LegacyRouteResult(
                dispatch=None,
                reason=f"{state.type.lower()} at finish stage",
                on_complete=Transition(phase="done", work_status="resolved"),
                on_return=RETURN_TO_EXECUTE,
            )
        # A stamped parent owns an integration branch (D-002): use branch
        # finish and require a resolution reference. The reference alone
        # is not proof of Git integration (D-003).
    return LegacyRouteResult(
        dispatch=LegacyDispatch("finish", "branch"),
        reason=f"{state.type} at finish stage",
        on_complete=Transition(
            phase="done",
            work_status="resolved",
            requires=("resolved_in", *_feature_children_requires(state)),
        ),
        on_return=RETURN_TO_EXECUTE,
    )


def _legacy_entry_phase(type: str, effort: str | None) -> str | None:
    if type != "TestGap":
        return "design"
    return None if effort is None else ("execute" if effort in {"xtra-small", "small"} else "plan")


def legacy_variant(state: RouteState) -> str | None:
    result = legacy_route(state)
    return None if result.dispatch is None else result.dispatch.variant

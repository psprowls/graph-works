"""The bond between `_rules.state._PHASE_COMPAT` and `workflow.route()`.

Epic spec §2.1's third argument -- the one that decided the routing table stays
at tier 3 -- is that *the lint rules encode the same state machine*. That
argument is only worth anything if the two cannot drift, so this test does the
work (C5-I). It is a bond, not a derivation: neither reads the other, and a
change to either that makes them disagree fails here.

**The precondition matters.** The property is not "no pair `route()` can produce
is incoherent" -- an item hand-edited to `work_status: accepted` at
`phase: design` is *already* incoherent, and `state.phase-status-incoherent`
reports it before any transition applies. The claim is the useful one: the
router never *introduces* incoherence into a state that was coherent going in.
`test_the_enumeration_is_not_vacuous` is what stops that precondition from
quietly filtering everything.
"""

from __future__ import annotations

from itertools import product

from work_tracker_okf._rules.state import _PHASE_COMPAT
from work_tracker_okf.hierarchy import ChildRollup
from work_tracker_okf.vocabulary import EFFORTS, PHASES, TYPES, WORK_STATUSES
from work_tracker_okf.workflow import PLAN_OR_EXECUTE, RouteState, Transition, route

_TYPES = sorted(TYPES)
_STATUSES = sorted(WORK_STATUSES)
_PHASES: list[str | None] = [None, *sorted(PHASES)]
_EFFORTS: list[str | None] = [None, *sorted(EFFORTS)]

#: Three rollups, because the epic execute gate reads one and two of its three
#: branches produce no transition at all. Without the satisfied rollup the
#: `execute -> finish` row is never exercised and the property is weaker than it
#: looks.
_ROLLUPS: list[ChildRollup | None] = [
    None,
    ChildRollup(total=2, terminal=2, open_paths=()),
    ChildRollup(total=2, terminal=1, open_paths=("work/release/children/bug-open",)),
]


def _coherent(status: str, phase: str | None) -> bool:
    """`_PHASE_COMPAT`'s own question, asked directly. An absent phase and an
    unconstrained status are both coherent, matching `state.coherence`."""
    allowed = _PHASE_COMPAT.get(status)
    return phase is None or allowed is None or phase in allowed


def _states() -> list[RouteState]:
    return [
        RouteState(
            type=type_name,
            work_status=status,
            phase=phase,
            effort=effort,
            child_rollup=rollup,
        )
        for type_name, status, phase, effort, rollup in product(_TYPES, _STATUSES, _PHASES, _EFFORTS, _ROLLUPS)
    ]


def _coherent_states() -> list[RouteState]:
    return [state for state in _states() if _coherent(state.work_status, state.phase)]


def _apply(pair: tuple[str, str | None], transition: Transition) -> tuple[str, str | None]:
    """A `None` field on a `Transition` means *unchanged*."""
    return (transition.work_status or pair[0], transition.phase or pair[1])


def test_no_transition_the_router_emits_lands_on_an_incoherent_pair() -> None:
    for state in _coherent_states():
        result = route(state)
        for transition in (result.on_dispatch, result.on_complete):
            if transition is None:
                continue
            landed = _apply((state.work_status, state.phase), transition)
            assert _coherent(*landed), (state, transition, landed)


def test_the_dispatch_then_complete_sequence_also_lands_coherent() -> None:
    """The transitions are applied in order in real life, not independently.
    Both readings must hold, and they are not the same claim: `on_dispatch`
    setting `in-progress` changes what `on_complete`'s bare `phase=` means."""
    for state in _coherent_states():
        result = route(state)
        pair: tuple[str, str | None] = (state.work_status, state.phase)
        for transition in (result.on_dispatch, result.on_complete):
            if transition is None:
                continue
            pair = _apply(pair, transition)
            assert _coherent(*pair), (state, transition, pair)


def test_the_enumeration_is_not_vacuous() -> None:
    """A precondition that silently filtered everything would make both
    properties above pass while asserting nothing."""
    transitions = 0
    phases_emitted: set[str] = set()
    for state in _coherent_states():
        result = route(state)
        for transition in (result.on_dispatch, result.on_complete):
            if transition is None:
                continue
            transitions += 1
            if transition.phase is not None:
                phases_emitted.add(transition.phase)
    assert transitions > 100
    assert phases_emitted >= {"design", "plan", "execute", "finish", "done", PLAN_OR_EXECUTE}


def test_the_gate_that_only_a_satisfied_rollup_opens_is_reached() -> None:
    """The `Epic` at `execute` produces a transition only when every child is
    terminal. Without `_ROLLUPS`' middle entry that row is unreachable."""
    state = RouteState(
        type="Epic",
        work_status="accepted",
        phase="execute",
        child_rollup=ChildRollup(total=2, terminal=2, open_paths=()),
    )
    result = route(state)
    assert result.on_complete is not None
    assert result.on_complete.phase == "finish"


def test_the_compat_map_keys_and_values_are_drawn_from_the_vocabulary() -> None:
    """A typo'd status silently constrains nothing, which is the failure mode a
    2-D map has and a 1-D enum does not."""
    assert set(_PHASE_COMPAT) <= WORK_STATUSES
    for status, phases in _PHASE_COMPAT.items():
        assert phases <= PHASES, status


def test_every_constrained_status_actually_constrains_something() -> None:
    """A key whose value is all of `PHASES` is a key that reports nothing."""
    for status, phases in _PHASE_COMPAT.items():
        assert phases < PHASES, status

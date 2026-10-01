"""State coherence derives from phase_compat; routing preserves coherent pairs."""

from __future__ import annotations

from datetime import date
from itertools import product
from pathlib import Path

from work_helpers import lane_report, write_item
from work_tracker_okf.hierarchy import ChildRollup
from work_tracker_okf.pipeline import phase_compat
from work_tracker_okf.vocabulary import EFFORTS, PHASES, TYPES, WORK_STATUSES
from work_tracker_okf.workflow import RouteState, Transition, route

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
    """`phase_compat()`'s own question, asked directly. An absent phase and an
    unconstrained status are both coherent, matching `state.coherence`."""
    allowed = phase_compat().get(status)
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
    assert phases_emitted >= {"design", "plan", "execute", "finish", "done"}


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
    assert set(phase_compat()) <= WORK_STATUSES
    for status, phases in phase_compat().items():
        assert phases <= PHASES, status


def test_every_constrained_status_actually_constrains_something() -> None:
    """A key whose value is all of `PHASES` is a key that reports nothing."""
    for status, phases in phase_compat().items():
        assert phases < PHASES, status


def test_the_rule_reports_exactly_the_pairs_phase_compat_rejects(tmp_path: Path) -> None:
    """One item per (status, phase) pair; the findings name exactly the
    incoherent ones. `Spike` has no path skips and no companions that matter here."""
    pairs = list(product(_STATUSES, sorted(PHASES)))
    for status, phase in pairs:
        write_item(tmp_path, f"spike-{status}-{phase}", f"type: Spike\nwork_status: {status}\nphase: {phase}\n")
    report = lane_report(tmp_path, today=date(2026, 8, 3))
    flagged = {f.path for f in report.by_code("state.phase-status-incoherent")}
    expected = {f"work/spike-{s}-{p}.md" for s, p in pairs if not _coherent(s, p)}
    assert flagged == expected
    assert expected  # non-vacuous: some pair is incoherent

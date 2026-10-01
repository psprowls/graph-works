"""The stage table and every accessor equal the literal each replaces (design §8)."""

from __future__ import annotations

import pytest
from work_tracker_okf import pipeline
from work_tracker_okf.pipeline import (
    ATTRIBUTE_ORDER,
    ATTRIBUTES,
    DECOMPOSING_TYPES,
    STAGE_TABLE,
    STAGES,
    VARIANT_REPLACEMENTS,
    child_gated,
    matches,
    variant_refusal,
)
from work_tracker_okf.vocabulary import BLAST_RADII, EFFORTS, PARENT_TYPES, PHASES, TYPES


def test_table_rows_are_the_four_stages_in_order() -> None:
    assert tuple(row.stage for row in STAGE_TABLE) == ("design", "plan", "execute", "finish")
    assert STAGES == ("design", "plan", "execute", "finish")


def test_vocabulary_phases_equal_table_plus_done() -> None:
    assert frozenset({*STAGES, "done"}) == PHASES


def test_phase_order_and_ordinals() -> None:
    assert pipeline.phase_order() == ("design", "plan", "execute", "finish", "done")
    assert dict(pipeline.phase_ordinals()) == {"design": 0, "plan": 1, "execute": 2, "finish": 3, "done": 4}


def test_hold_phases_equals_decisions_literal() -> None:
    assert pipeline.hold_phases() == frozenset(PHASES | {"entry"})


def test_code_reader_results_ledger_and_dispatch_phases() -> None:
    assert pipeline.code_phases() == frozenset({"execute", "finish"})
    assert pipeline.read_only_phases() == frozenset({"design", "plan"})
    assert pipeline.results_phases() == frozenset({"execute", "finish"})
    assert pipeline.ledger_phases() == frozenset({"plan", "execute", "finish", "done"})
    assert pipeline.dispatch_phases() == PHASES - {"done"}


def test_expected_phases_is_none_plus_phase_order() -> None:
    assert pipeline.expected_phases() == ("none", "design", "plan", "execute", "finish", "done")


@pytest.mark.parametrize("type_", sorted(TYPES))
@pytest.mark.parametrize("phase", [None, *sorted(PHASES)])
def test_child_gated_equals_the_hierarchy_literal(type_: str, phase: str | None) -> None:
    assert child_gated(type_, phase) == (type_ in PARENT_TYPES and phase in {"execute", "finish"})


def test_decomposing_types() -> None:
    assert frozenset({"Epic", "Release"}) == DECOMPOSING_TYPES
    assert DECOMPOSING_TYPES <= PARENT_TYPES


def test_attribute_vocabulary_has_spec_stale_and_no_variant() -> None:
    assert set(ATTRIBUTES) == {"stage", "type", "effort", "blast_radius", "has_spec", "has_plan", "spec_stale"}
    assert ATTRIBUTES["stage"] == frozenset(STAGES)
    assert ATTRIBUTES["type"] == TYPES
    assert ATTRIBUTES["effort"] == EFFORTS
    assert ATTRIBUTES["blast_radius"] == BLAST_RADII
    assert ATTRIBUTES["has_spec"] is None and ATTRIBUTES["has_plan"] is None and ATTRIBUTES["spec_stale"] is None


def test_attribute_order_covers_each_finite_domain() -> None:
    assert ATTRIBUTE_ORDER["effort"] == ("xtra-small", "small", "medium", "large", "xtra-large")
    assert set(ATTRIBUTE_ORDER["effort"]) == EFFORTS
    assert set(ATTRIBUTE_ORDER["blast_radius"]) == BLAST_RADII


@pytest.mark.parametrize(
    ("spec", "attrs", "expected"),
    [
        ({}, {}, True),
        ({"stage": "design"}, {"stage": "design"}, True),
        ({"stage": "design"}, {"stage": "plan"}, False),
        ({"type": ("Epic", "Release")}, {"type": "Release"}, True),
        ({"has_spec": True}, {"has_spec": True}, True),
        ({"has_spec": True}, {"has_spec": False}, False),
        ({"has_spec": False}, {"has_spec": None}, False),
        ({"effort": "small"}, {}, False),
        ({"has_plan": True}, {"has_plan": "true"}, False),
        ({"stage": "plan", "spec_stale": True}, {"stage": "plan", "spec_stale": True}, True),
    ],
)
def test_matches(spec, attrs, expected) -> None:
    assert matches(spec, attrs) is expected


def test_every_retired_variant_has_its_replacement() -> None:
    assert VARIANT_REPLACEMENTS == {
        "exploration": "{stage: design} (the general rule; order it before type-specific rules)",
        "diagnosis": "{stage: design, type: Bug}",
        "epic-design": "{stage: design, type: [Epic, Release]}",
        "reconcile": "two rules: {stage: design, has_spec: true} and {stage: plan, spec_stale: true}",
        "single": "{stage: plan}",
        "decompose": "{stage: plan, type: [Epic, Release]}",
        "unplanned": "{stage: execute, has_plan: false}",
        "planned": "{stage: execute, has_plan: true}",
        "branch": "{stage: finish}",
    }


@pytest.mark.parametrize("variant", sorted(VARIANT_REPLACEMENTS))
def test_variant_refusal_names_the_replacement(variant: str) -> None:
    message = variant_refusal(variant)
    assert message.startswith("match.variant: routing variants are retired; replace with ")
    assert f"{variant} -> {VARIANT_REPLACEMENTS[variant]}" in message


def test_variant_refusal_names_every_listed_value_and_unknowns() -> None:
    message = variant_refusal(["diagnosis", "reconcile", "planned", "unplanned", "bogus"])
    for value in ("diagnosis", "reconcile", "planned", "unplanned"):
        assert f"{value} -> {VARIANT_REPLACEMENTS[value]}" in message
    assert "'bogus' -> not a routing variant; remove it" in message


def test_consumers_read_the_table() -> None:
    import typing

    from work_tracker_okf import advance, decisions, dependencies, paths, placement

    assert pipeline.phase_order() == dependencies.PHASE_ORDER
    assert frozenset({"design", "plan", "execute", "finish"}) == dependencies.BLOCKS
    assert dependencies.BLOCKS | {"resolved"} == dependencies.NEEDS
    assert dict(paths.PHASE_ORDINALS) == {"design": "01", "plan": "02", "execute": "03", "finish": "04"}
    assert pipeline.code_phases() == placement.CODE_PHASES
    assert pipeline.read_only_phases() == placement.READER_PHASES
    assert pipeline.hold_phases() == decisions.HOLD_PHASES
    assert typing.get_args(advance.ExpectedPhase) == pipeline.expected_phases()
    assert not hasattr(dependencies, "entry_phase")


def test_single_stage_constants_are_bound_from_stages_by_position() -> None:
    assert (pipeline.DESIGN, pipeline.PLAN, pipeline.EXECUTE, pipeline.FINISH) == pipeline.STAGES


def test_expected_phase_literal_equals_expected_phases() -> None:
    from typing import get_args

    from work_tracker_okf import advance

    assert get_args(pipeline.ExpectedPhase) == pipeline.expected_phases()
    assert advance.ExpectedPhase is pipeline.ExpectedPhase

"""Routing entry points honour a caller-supplied pipeline definition (epic D-010)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from work_helpers import make_item
from work_tracker_okf.advance import AdvancePlan, advance
from work_tracker_okf.pipeline import PACKAGED_DEFINITION, PipelineDefinition, parse_path_rules
from work_tracker_okf.placement import ReaderObservation, plan_reader_receipt

TODAY = date(2026, 9, 30)
OBSERVATION = ReaderObservation("task_1", "ctx_2", "key-3", "graph-works", str(Path(Path.cwd().anchor, "wt")), "a" * 40)


def _with(*rules: dict[str, object]) -> PipelineDefinition:
    return replace(
        PACKAGED_DEFINITION,
        path_rules=PACKAGED_DEFINITION.path_rules + parse_path_rules(list(rules), source="test"),
    )


SKIP_PLAN = _with({"match": {"type": "Feature"}, "stages": ["design", "execute", "finish"]})
ENTER_AT_PLAN = _with({"match": {"type": "Feature"}, "stages": ["plan", "execute", "finish"]})


def _phase_change(plan: AdvancePlan) -> object:
    assert plan.refusal is None
    return next(change.after for change in plan.changes if change.key == "phase")


def test_advance_routes_design_completion_by_the_given_definition() -> None:
    item = make_item("work/feature-a", type="Feature", phase="design", effort="medium", has_design_artifact=True)
    assert _phase_change(advance((item,), item.path, today=TODAY)) == "plan"
    assert _phase_change(advance((item,), item.path, today=TODAY, definition=SKIP_PLAN)) == "execute"


def test_reader_receipt_entry_phase_uses_the_given_definition() -> None:
    item = make_item("work/feature-a", type="Feature", phase=None, work_status="open", effort="medium")
    packaged = plan_reader_receipt((item,), item.path, root=item.path, phase="plan", observation=OBSERVATION)
    custom = plan_reader_receipt(
        (item,), item.path, root=item.path, phase="plan", observation=OBSERVATION, definition=ENTER_AT_PLAN
    )
    assert packaged.refusal == "phase-mismatch"
    assert packaged.current_phase == "design"
    assert custom.refusal is None
    assert custom.current_phase == "plan"

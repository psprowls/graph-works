"""The variant table and variant choice frozen at 25f7d0f6. Parity oracle only."""

from __future__ import annotations

from graph_works_core.workspace.pipeline import ATTEND_TAIL, EXECUTE_TAIL, WORKSPACE_COMMIT_TAIL
from work_tracker_okf.workflow import RouteState

LEGACY_PIPELINE = {
    "exploration": ("superpowers:brainstorming", "attend", ATTEND_TAIL),
    "diagnosis": ("superpowers:systematic-debugging", "attend", ATTEND_TAIL),
    "reconcile": ("gw:reconciling-spec", "autonomous", WORKSPACE_COMMIT_TAIL),
    "epic-design": ("gw:epic-design", "attend", ATTEND_TAIL),
    "decompose": ("gw:planning-epics", "autonomous", WORKSPACE_COMMIT_TAIL),
    "single": ("superpowers:writing-plans", "autonomous", WORKSPACE_COMMIT_TAIL),
    "planned": ("superpowers:subagent-driven-development", "autonomous", EXECUTE_TAIL),
    "unplanned": ("superpowers:test-driven-development", "autonomous", EXECUTE_TAIL),
    "branch": ("superpowers:finishing-a-development-branch", "relay", None),
}


def legacy_variant(state: RouteState, stage: str) -> str:
    """Baseline `_design_variant` / `_plan` / `_execute` / `_finish` variant choice."""
    if stage == "design":
        if state.has_spec_doc:
            return "reconcile"
        if state.type in {"Release", "Epic"}:
            return "epic-design"
        return "diagnosis" if state.type == "Bug" else "exploration"
    if stage == "plan":
        if state.phase == "plan" and state.stale_spec:
            return "reconcile"
        return "decompose" if state.type in {"Release", "Epic"} else "single"
    if stage == "execute":
        return "planned" if state.has_plan_doc else "unplanned"
    return "branch"

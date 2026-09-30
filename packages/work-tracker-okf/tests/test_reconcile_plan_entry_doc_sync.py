"""Pin skill wording to the plan-phase reconciliation route."""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML
from work_tracker_okf.workflow import RouteState, Transition, route

PLUGIN = Path(__file__).resolve().parents[3] / "plugins" / "gw" / "skills"


def _text(name: str) -> str:
    path = PLUGIN / name / "SKILL.md"
    if not path.is_file():
        pytest.skip(f"{name} skill not present in this checkout")
    return " ".join(path.read_text(encoding="utf-8").split())


def test_the_route_is_what_the_skills_describe() -> None:
    result = route(RouteState(type="Feature", work_status="open", phase="plan", effort="medium", stale_spec=("x",)))
    assert result.on_complete == Transition(phase="plan", stamp_baseline=True)


def test_reconciling_spec_documents_the_plan_phase_entry() -> None:
    text = _text("reconciling-spec")
    assert "phase: plan" in text
    assert "keeps `phase: plan`" in text
    assert "spec_baseline" in text
    assert "Landed since your design" in text


def test_workflow_documents_the_same_phase_completion() -> None:
    assert "same-phase completion" in _text("workflow")


def test_reconciling_spec_frontmatter_is_valid_yaml() -> None:
    path = PLUGIN / "reconciling-spec" / "SKILL.md"
    if not path.is_file():
        pytest.skip("skill not present")
    frontmatter = path.read_text(encoding="utf-8").split("---", 2)[1]
    parsed = YAML(typ="safe").load(frontmatter)
    assert parsed["name"] == "reconciling-spec"
    assert "phase: plan" in parsed["description"]

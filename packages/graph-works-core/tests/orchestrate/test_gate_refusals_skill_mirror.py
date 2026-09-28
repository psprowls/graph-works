"""Every fail-closed gate code is one the skills tell a human how to handle."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from work_tracker_okf.advance import COMMIT_GATE_REFUSALS


def _find(relative: str) -> Path | None:
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / relative
        if candidate.is_file():
            return candidate
    return None


def _section(text: str, step: int) -> str:
    match = re.search(rf"### {step}\. .*?\n(.*?)(?=\n### |\Z)", text, re.DOTALL)
    assert match is not None
    return match.group(1)


def test_workflow_step_5_names_every_gate_code_and_the_bypass() -> None:
    skill = _find("plugins/gw/skills/workflow/SKILL.md")
    if skill is None:
        pytest.skip("workflow SKILL.md is not present in this checkout")
    step5 = _section(skill.read_text(encoding="utf-8"), 5)
    assert sorted(code for code in COMMIT_GATE_REFUSALS if f"`{code}`" not in step5) == []
    assert "--skip-gate" in step5 and "never pass" in step5


def test_workflow_step_3_records_the_execute_baseline() -> None:
    skill = _find("plugins/gw/skills/workflow/SKILL.md")
    if skill is None:
        pytest.skip("workflow SKILL.md is not present in this checkout")
    assert "gw work record-baseline" in _section(skill.read_text(encoding="utf-8"), 3)


def test_auto_drive_routes_a_gate_refusal_to_the_human() -> None:
    skill = _find("plugins/gw/skills/auto-drive/SKILL.md")
    if skill is None:
        pytest.skip("auto-drive SKILL.md is not present in this checkout")
    text = skill.read_text(encoding="utf-8")
    assert "gate refused:" in text and "--skip-gate" in text and "--actor" in text

"""Every verb a conformance driver runs is one the skill it models tells an agent to run."""

from __future__ import annotations

from pathlib import Path

import pytest
from conformance_drivers import RunLog
from conformance_fixture import Fixture

RETIRED = ("record-placement", "prepare-workspace", "record-baseline", "merge-workspace")


def _skill(name: str, relative: str = "SKILL.md") -> str:
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "plugins/gw/skills" / name / relative
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    pytest.skip(f"{name}/{relative} is not present in this checkout")


def _missing(log: RunLog, text: str) -> list[str]:
    verbs = [f"gw {verb}" for verb in sorted(set(log.verbs)) if f"gw {verb}" not in text]
    modes = [mode for mode in sorted(log.place_modes) if mode not in text]
    scripts = [script for script in sorted(set(log.scripts)) if script not in text]
    return [*verbs, *modes, *scripts]


def test_every_attended_verb_is_in_the_workflow_skill(attended: tuple[Fixture, RunLog]) -> None:
    # Step 5 defers the finish procedure to the finishing skill and the cleanup plan to its reference.
    text = _skill("workflow") + _skill("finishing-relay") + _skill("finishing-relay", "references/finish-cleanup.md")
    assert _missing(attended[1], text) == []


def test_every_auto_drive_verb_is_in_its_skills(auto_driven: tuple[Fixture, RunLog]) -> None:
    text = "".join(_skill(name) for name in ("auto-drive", "finishing-relay", "workflow"))
    assert _missing(auto_driven[1], text) == []


@pytest.mark.parametrize("skill", ["workflow", "auto-drive", "finishing-relay"])
def test_no_skill_names_a_retired_placement_verb(skill: str) -> None:
    text = _skill(skill)
    assert [verb for verb in RETIRED if verb in text] == [], skill

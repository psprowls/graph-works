"""The placement refusal vocabulary and the worker placement line <-> the skills that act on them.

`REFUSAL_KINDS` (the placement engine's) is what `gw work dispatch` reports as
`placement-refused`, and auto-drive's hold recipe must name every kind. Reads the
plugin tree only. Absence of the plugin tree is a skip, as in
`test_orchestrate_blocked_kinds_skill_mirror.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from gw_orchestrate.commands import WORKER_PLACEMENT_LINE
from gw_orchestrate.placing import REFUSAL_KINDS

HOLD_RECIPE = "**The hold recipe for `placement-refused`.**"

#: Placement vocabulary computed placement deleted: the planner's action words, anchor
#: preparations, reader checkouts and their receipts, the launch adapter's commands and
#: the backend provisioning flag. `preparations` is checked as a word on its own, so
#: the surviving `workspace_preparations[]` field and its "Workspace preparations"
#: paragraph (child 5's) do not count.
DELETED_VOCABULARY = (
    "pin-detached",
    "fork-child",
    "create-top-level",
    "record-reader",
    "settle-placement",
    "prepare-reader",
    "worktree-unsupported",
    "provisions_worktrees",
)
_PREPARATIONS = re.compile(r"(?<!Workspace )\bpreparations\b")


def _find(relative: str) -> Path | None:
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / relative
        if candidate.is_file():
            return candidate
    return None


def _auto_drive() -> str:
    skill = _find("plugins/gw/skills/auto-drive/SKILL.md")
    if skill is None:
        pytest.skip("auto-drive SKILL.md is not present in this checkout")
    return skill.read_text(encoding="utf-8")


def test_every_engine_refusal_is_in_the_hold_recipe() -> None:
    text = _auto_drive()
    assert HOLD_RECIPE in text
    # The recipe runs to the next bold-led paragraph or heading.
    recipe = re.split(r"\n(?:\*\*|#)", text.split(HOLD_RECIPE, 1)[1], maxsplit=1)[0]
    missing = sorted(kind for kind in REFUSAL_KINDS if f"`{kind}`" not in recipe)
    assert missing == []
    assert "PLACEMENT REFUSED <key>: <refusal.kind>: <refusal.detail>" in recipe
    assert "gw work decision add <item-path> --status open" in recipe


def test_the_failure_table_routes_placement_refused_to_the_hold_recipe() -> None:
    text = _auto_drive()
    row = next(line for line in text.splitlines() if line.startswith("| `placement-refused` |"))
    assert "hold recipe" in row


def test_the_failure_table_no_longer_points_at_dispatch_checks() -> None:
    assert "dispatch-checks.md" not in _auto_drive()


def test_skill_names_no_deleted_placement_vocabulary() -> None:
    text = _auto_drive()
    present = [word for word in DELETED_VOCABULARY if word in text]
    assert present == []
    assert _PREPARATIONS.findall(text) == []


def test_the_preparations_word_check_sees_the_deleted_field() -> None:
    assert _PREPARATIONS.findall("`preparations[]` contains `owner_path`")
    assert _PREPARATIONS.findall("after the code preparations")
    assert not _PREPARATIONS.findall("`workspace_preparations[]` and **Workspace preparations.**")


def test_the_worker_placement_line_and_workflow_skill_agree() -> None:
    skill = _find("plugins/gw/skills/workflow/SKILL.md")
    if skill is None:
        pytest.skip("workflow SKILL.md is not present in this checkout")
    text = skill.read_text(encoding="utf-8")
    assert "--no-infer-worktree" not in WORKER_PLACEMENT_LINE
    assert "gw work place" in WORKER_PLACEMENT_LINE
    step2 = re.search(r"### 2\. .*?\n(.*?)(?=\n### |\Z)", text, re.DOTALL)
    assert step2 is not None
    assert "gw work place <work-path> --ensure --json" in step2.group(1)
    assert "--no-infer-worktree" not in text


def test_every_start_refusal_is_named_where_the_workflow_places_the_item() -> None:
    skill = _find("plugins/gw/skills/workflow/SKILL.md")
    if skill is None:
        pytest.skip("workflow SKILL.md is not present in this checkout")
    text = skill.read_text(encoding="utf-8")
    paragraph = text.split("**Placement (attended sessions only).**", 1)[1].split("\n\n", 1)[0]
    start = (
        "wrong-phase",
        "already-started",
        "refresh-conflict",
        "dirty-worktree",
        "foreign-checkout",
        "occupied",
        "parent-unresolved",
        "timeout",
        "git-unavailable",
    )
    assert [code for code in start if f"`{code}`" not in paragraph] == []

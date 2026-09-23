"""`PLACEMENT_REFUSALS` and the worker placement line <-> the skills that act on them.

Reads the plugin tree only. Absence of the plugin tree is a skip, as in
`test_orchestrate_blocked_kinds_skill_mirror.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from graph_works_core.orchestrate.commands import WORKER_PLACEMENT_LINE
from work_tracker_okf.placement import PLACEMENT_REFUSALS


def _find(relative: str) -> Path | None:
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / relative
        if candidate.is_file():
            return candidate
    return None


def test_every_placement_refusal_is_handled_in_dispatch_mechanics() -> None:
    skill = _find("plugins/gw/skills/auto-drive/SKILL.md")
    if skill is None:
        pytest.skip("auto-drive SKILL.md is not present in this checkout")
    section = re.search(r"## 3\. Dispatch mechanics\n(.*?)\n## 4\. ", skill.read_text(encoding="utf-8"), re.DOTALL)
    assert section is not None
    refused = section.group(1).split("**Refused.**", 1)[-1].split("**Application failed", 1)[0]
    missing = sorted(kind for kind in PLACEMENT_REFUSALS if f"`{kind}`" not in refused)
    assert missing == []


@pytest.mark.parametrize("step", [2, 5])
def test_the_worker_placement_flag_is_what_the_workflow_skill_tells_workers(step: int) -> None:
    skill = _find("plugins/gw/skills/workflow/SKILL.md")
    if skill is None:
        pytest.skip("workflow SKILL.md is not present in this checkout")
    assert "--no-infer-worktree" in WORKER_PLACEMENT_LINE
    section = re.search(
        rf"### {step}\. .*?\n(.*?)(?=\n### |\Z)",
        skill.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert section is not None
    assert "`--no-infer-worktree`" in section.group(1)


@pytest.mark.parametrize(
    ("start", "end", "required"),
    [
        (
            "**Bind.**",
            "**Verify the branch.**",
            (
                "binds to this `task_id`/`dispatch_id`",
                "latest attempt (§2.1's definition) is this Dispatch",
                "frozen dispatch key",
                "newer attempt supersedes",
                "enter inspection",
            ),
        ),
        (
            "**Verify the branch.**",
            "**Record, or skip.**",
            (
                "stripping `refs/heads/`",
                "git -C <observed path> branch --show-current",
                "Empty output (detached HEAD)",
                "unverifiable evidence",
                "halt into §4.2",
            ),
        ),
        (
            "**Check.**",
            "**Refused.**",
            (
                "Success is exit 0 with `refusal: null`",
                "`after` equal to the observation",
                "same command plus `--dry-run`",
                "`changed: false`",
                "receipt reference",
                "never store a preamble or a dispatch capability",
            ),
        ),
        (
            "**Refused.**",
            "**Application failed or other non-success.**",
            (
                "PLACEMENT UNRECORDED <key>: <refusal.reason> — <refusal.detail>",
                "halt this item into inspection",
                "`phase-mismatch`",
                "finished its stage first",
                "Do not call `gw work advance` to stamp it",
                "do not re-record with the new phase",
                "do not start another fork",
                "not stop or release the worker",
            ),
        ),
        (
            "**Application failed or other non-success.**",
            "A lost response is not a refusal",
            (
                "including when `refusal: null`",
                "failed application",
                "nonzero exit",
                "malformed or missing JSON",
                "mismatched `after`",
                "failed dry-run verification",
                "inspection",
                "Do not call `gw work advance` to stamp it",
                "do not start another fork",
                "do not re-record with a new phase",
                "do not stop or release the worker",
                "§4.1.1",
            ),
        ),
        (
            "A lost response is not a refusal",
            "5. **",
            (
                "timeout, disconnect or restart",
                "enter inspection",
                "Do not call `gw work advance` to stamp it",
                "do not start another fork",
                "repeat step 1 and the `--dry-run` read",
                "before recording again",
                "attempt, phase and observation",
                "submission probe runs only after recording succeeded",
            ),
        ),
    ],
    ids=[
        "attempt-binding",
        "branch-verification",
        "success-receipt",
        "semantic-refusal",
        "non-success-without-refusal",
        "lost-response",
    ],
)
def test_placement_inspection_paths_are_explicit(start: str, end: str, required: tuple[str, ...]) -> None:
    skill = _find("plugins/gw/skills/auto-drive/SKILL.md")
    if skill is None:
        pytest.skip("auto-drive SKILL.md is not present in this checkout")
    mechanics = skill.read_text(encoding="utf-8").split("## 3. Dispatch mechanics\n", 1)[1]
    mechanics = mechanics.split("\n## 4. ", 1)[0]
    assert start in mechanics
    section = mechanics.split(start, 1)[1]
    assert end in section
    section = " ".join(section.split(end, 1)[0].split())
    for phrase in required:
        assert phrase in section

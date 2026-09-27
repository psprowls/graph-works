"""`PLACEMENT_REFUSALS` and the worker placement line <-> the skills that act on them.

Reads the plugin tree only. Absence of the plugin tree is a skip, as in
`test_orchestrate_blocked_kinds_skill_mirror.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from graph_works_core.orchestrate.commands import WORKER_PLACEMENT_LINE
from subagents_io.dispatch import WORKTREE_ACTIONS
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


def test_worktree_actions_match_the_dispatch_shape_and_assertion_table() -> None:
    skill = _find("plugins/gw/skills/auto-drive/SKILL.md")
    if skill is None:
        pytest.skip("auto-drive SKILL.md is not present in this checkout")
    text = skill.read_text(encoding="utf-8")
    shape = text.split("`worktree` (`action`: ", 1)[1].split(",", 1)[0]
    assert set(re.findall(r"`([a-z-]+)`", shape)) == WORKTREE_ACTIONS
    fields = " ".join(text.split("`worktree` (`action`: ", 1)[1].split("`auto_merge`", 1)[0].split())
    assert "`start_sha`, a full 40- or 64-character lowercase hex commit object ID for `pin-detached`" in fields
    assert '`null` otherwise; `branch` is `null` for `pin-detached`, never `HEAD` or `""`' in fields
    table = text.split("| Planned `worktree.action` | Assertion |", 1)[1].split("\n\n", 1)[0]
    actions = set()
    for row in table.splitlines()[1:]:
        actions.update(re.findall(r"`([a-z-]+)`", row.split("|", 2)[1]))
    assert actions == WORKTREE_ACTIONS


@pytest.mark.parametrize(
    ("start", "end", "required"),
    [
        (
            "**Reader preparation (`pin-detached`).**",
            "Then encode the immutable task spec",
            (
                "fresh random UUID for every new dispatch attempt",
                "durably save it before preparation",
                "distinct from the later Orca `dispatchId`",
                "--attempt-id <preparation-attempt-id>",
                "--out-placement <fresh-placement-json>",
                "> <workspace>/okf/<dispatch path>/references/orca-placement/<key>.json",
                "fresh output path for every invocation, including recovery",
                "Require exit 0 before encode, task-create or launch",
                "File existence alone never authorizes launch",
                "same conclusively unlaunched allocation",
                "authoritative Orca request/dispatch state",
                "Never share a previously launched checkout",
                "never re-detach",
                "PREPARATION REFUSED <key>",
                "Before task-create, use only §4.2.1's pre-task decision flow",
                "When a Task already exists (resume/retry), use the existing-Task failure question (§4.2)",
                "exact prepared path",
                "attempt_id",
            ),
        ),
        (
            "**Verify the branch.**",
            "**Record, or skip.**",
            ("For non-reader actions", "For `pin-detached`", "detached HEAD", "`start_sha`"),
        ),
        (
            "**Record, or skip.**",
            "**Check.**",
            (
                "A `pin-detached` dispatch — root or descendant — records a reader receipt",
                "gw work record-reader <slug> --root <work-path> --phase <dispatch phase>",
                "--task-id <task_id> --dispatch-id <dispatch_id> --dispatch-key <key>",
                "--repo <dispatch repo.name> --worktree <observed path> --start-sha <start_sha> --json",
                "actual task/dispatch IDs",
                "Success is exit 0 with `written` or `replayed` true",
                "`receipt_path` and `start_sha`",
                "`refusal`/`attempt-mismatch`",
                "PLACEMENT UNRECORDED <key>",
                "Never call `record-placement` for a `pin-detached` dispatch",
                "root's placement is recorded only at `execute`/`finish`",
            ),
        ),
        (
            "**Resuming a previously-parked item.**",
            "### 2.6.1",
            (
                "For `pin-detached`",
                "fresh preparation identity",
                "never reuse the parked reader's checkout",
                "original saved reader dispatch input and preparation evidence",
                "`start_sha` matches the baseline in the frozen Task prompt",
                "Missing, malformed or mismatched original evidence refuses resume",
                "Never substitute this cycle's planner baseline",
                "Use that original dispatch JSON for `prepare-reader`",
                "`settle-placement --dispatch <original-saved-reader-dispatch-json>`",
                "this resume attempt's successful preparation result",
                "Record the reader receipt with the original `start_sha`",
            ),
        ),
        (
            "### 4.2.1 Reader preparation failure before Task creation",
            "### 4.3",
            (
                "exactly two options — *retry preparation* / *stop the run*",
                "Do not offer Skip",
                "never call `task-update` or invent task/dispatch IDs",
                "Save the question and answer in the preparation attempt's evidence",
                "Preserve the dispatch input, identity, invocation paths, exit status, stdout/stderr",
                "leave any allocated checkout visible",
                "same conclusively unlaunched allocation",
                "fresh preparation identity for a new attempt",
                "fresh output path",
                "saved dispatch input",
                "Require exit 0 before encode, task-create or launch",
                "exit the coordinator loop",
                "Do not return to planning and automatically re-propose this key",
            ),
        ),
        (
            "### Failure question",
            "- **Skip**:",
            (
                "This question requires an existing Task",
                "task-create goes to §4.2.1 instead",
                "For `pin-detached`",
                "fresh preparation identity",
                "never reuse a previously launched reader checkout",
                "authoritative Orca request/dispatch state",
                "same conclusively unlaunched allocation",
                "fresh output path",
            ),
        ),
    ],
    ids=[
        "reader-preparation",
        "detached-verification",
        "reader-receipt",
        "reader-resume",
        "reader-pre-task-failure",
        "reader-retry",
    ],
)
def test_reader_procedure_contract_is_explicit(start: str, end: str, required: tuple[str, ...]) -> None:
    skill = _find("plugins/gw/skills/auto-drive/SKILL.md")
    if skill is None:
        pytest.skip("auto-drive SKILL.md is not present in this checkout")
    text = skill.read_text(encoding="utf-8")
    assert start in text
    section = text.split(start, 1)[1]
    assert end in section
    section = " ".join(section.split(end, 1)[0].split())
    for phrase in required:
        assert phrase in section


def test_readers_are_settled_and_reported_at_the_prepared_commit() -> None:
    skill = _find("plugins/gw/skills/auto-drive/SKILL.md")
    if skill is None:
        pytest.skip("auto-drive SKILL.md is not present in this checkout")
    text = skill.read_text(encoding="utf-8")
    reader_row = next(line for line in text.splitlines() if line.startswith("   | `pin-detached` |"))
    for phrase in ("settle-placement", "prepared path", "HEAD", "start_sha", "detached", "clean"):
        assert phrase in reader_row
    assert "dispatched <key> -> <observed path> detached at <start_sha>" in text
    assert "reads from the shared epic worktree" not in text
    assert "read-only descendant that records nothing" not in text

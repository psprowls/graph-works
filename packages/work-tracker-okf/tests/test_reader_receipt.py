"""Reader receipts observe a detached checkout without changing code stamps."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from work_helpers import make_item
from work_tracker_okf.items import WorkItem
from work_tracker_okf.placement import READER_REFUSALS, ReaderObservation, plan_placement, plan_reader_receipt

ROOT = "work/epic-a"
CHILD = f"{ROOT}/children/feature-a"
OTHER = "work/bug-other"
WORKTREE = str(Path(Path.cwd().anchor, "wt", "reader"))
OBSERVATION = ReaderObservation("task_1", "ctx_2", "key-3", "graph-works", WORKTREE, "a" * 40)


def _items(phase: str = "design") -> tuple[WorkItem, ...]:
    return (
        make_item(ROOT, type="Epic", phase=phase, work_status="in-progress", effort="medium", child_paths=(CHILD,)),
        make_item(
            CHILD,
            type="Feature",
            phase=phase,
            work_status="in-progress",
            effort="medium",
            parent_path=ROOT,
            ancestor_paths=(ROOT,),
        ),
        make_item(OTHER, type="Bug", phase=phase, work_status="in-progress", effort="medium"),
    )


@pytest.mark.parametrize("phase", ["design", "plan"])
@pytest.mark.parametrize("path", [ROOT, CHILD])
def test_root_and_descendant_readers_are_accepted(path: str, phase: str) -> None:
    plan = plan_reader_receipt(_items(phase), path, root=ROOT, phase=phase, observation=OBSERVATION)
    assert plan.refusal is None, plan.detail
    assert (plan.path, plan.root, plan.expected_phase, plan.current_phase) == (path, ROOT, phase, phase)
    assert plan.observation is OBSERVATION


@pytest.mark.parametrize("phase", ["execute", "finish"])
def test_code_phases_refuse_a_reader(phase: str) -> None:
    assert (
        plan_reader_receipt(_items(phase), CHILD, root=ROOT, phase=phase, observation=OBSERVATION).refusal
        == "code-phase"
    )


@pytest.mark.parametrize(
    ("path", "root", "refusal"),
    [("work/missing", ROOT, "unknown-path"), (CHILD, "work/missing", "unknown-root"), (OTHER, ROOT, "outside-root")],
)
def test_path_and_root_refusals(path: str, root: str, refusal: str) -> None:
    assert plan_reader_receipt(_items(), path, root=root, phase="design", observation=OBSERVATION).refusal == refusal


def test_invalid_item_precedes_observation_validation() -> None:
    items = tuple(replace(item, type="") if item.path == CHILD else item for item in _items())
    bad = replace(OBSERVATION, start_sha="HEAD")
    assert plan_reader_receipt(items, CHILD, root=ROOT, phase="design", observation=bad).refusal == "invalid-item"


@pytest.mark.parametrize("phase", ["done", "review", ""])
def test_invalid_phase_refuses(phase: str) -> None:
    assert (
        plan_reader_receipt(_items(), CHILD, root=ROOT, phase=phase, observation=OBSERVATION).refusal == "invalid-phase"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worktree", "relative/wt"),
        ("start_sha", "HEAD"),
        ("start_sha", "a" * 39),
        ("start_sha", "A" * 40),
        ("dispatch_id", "ctx/2"),
        ("dispatch_id", "ctx..2"),
        ("repo", ""),
        ("task_id", " "),
        ("dispatch_key", "key\n3"),
    ],
)
def test_bad_observations_refuse(field: str, value: str) -> None:
    observation = replace(OBSERVATION, **{field: value})
    assert (
        plan_reader_receipt(_items(), CHILD, root=ROOT, phase="design", observation=observation).refusal
        == "invalid-observation"
    )


def test_reader_receipt_accepts_sha256_object_ids() -> None:
    observation = replace(OBSERVATION, start_sha="b" * 64)
    assert plan_reader_receipt(_items(), CHILD, root=ROOT, phase="design", observation=observation).refusal is None


def test_terminal_precedes_phase_mismatch() -> None:
    items = tuple(
        replace(item, phase="done", work_status="resolved") if item.path == CHILD else item for item in _items()
    )
    assert plan_reader_receipt(items, CHILD, root=ROOT, phase="plan", observation=OBSERVATION).refusal == "terminal"


def test_phase_mismatch_refuses() -> None:
    assert (
        plan_reader_receipt(_items("execute"), CHILD, root=ROOT, phase="plan", observation=OBSERVATION).refusal
        == "phase-mismatch"
    )


def test_never_entered_unprovable_item_refuses() -> None:
    items = tuple(
        replace(item, phase=None, work_status="accepted") if item.path == CHILD else item for item in _items()
    )
    assert (
        plan_reader_receipt(items, CHILD, root=ROOT, phase="design", observation=OBSERVATION).refusal
        == "entry-unprovable"
    )


def test_reader_plan_has_no_stamp_changes_and_detached_placement_still_refuses() -> None:
    items = tuple(replace(item, worktree="/wt/old", branch="old", updated="2026-09-01") for item in _items())
    plan = plan_reader_receipt(items, CHILD, root=ROOT, phase="design", observation=OBSERVATION)
    assert plan.refusal is None
    assert not hasattr(plan, "changes") and not hasattr(plan, "before")
    assert [(item.worktree, item.branch, item.updated) for item in items] == [("/wt/old", "old", "2026-09-01")] * 3
    placement = plan_placement(
        items, CHILD, root=ROOT, phase="execute", worktree=WORKTREE, branch="HEAD", today=date(2026, 9, 25)
    )
    assert placement.refusal == "invalid-pair" and placement.changes == ()


def test_reader_refusal_vocabulary_is_closed() -> None:
    assert {
        "unknown-path",
        "unknown-root",
        "outside-root",
        "invalid-item",
        "invalid-phase",
        "invalid-observation",
        "code-phase",
        "terminal",
        "entry-unprovable",
        "phase-mismatch",
    } == READER_REFUSALS

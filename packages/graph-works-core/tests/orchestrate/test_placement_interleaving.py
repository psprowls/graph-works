"""Placement records serialize with advance and hold filing on the owner lock."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import placement
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.work import commands as work
from graph_works_core.workspace import decision_owner as owners
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_io import load
from work_tracker_okf.decisions import ledger_ref
from work_tracker_okf.sources import upsert

TODAY = date(2026, 9, 14)
ITEM = "work/feature-race"
WT, BR = str(Path(Path.cwd().anchor, "wt", "race")), "psprowls/feature-race-1a2b3c4d"
WAIT = 10  # seconds; a ceiling on a hang, never a synchronization point


class _Run(threading.Thread):
    def __init__(self, target: Callable[[], Any]) -> None:
        super().__init__(daemon=True)
        self._target_fn = target
        self.result: Any = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self._target_fn()
        except BaseException as exc:
            self.error = exc


def _layout(tmp_path: Path, *, work_status: str = "in-progress", owner: str = "owner: pat\n") -> WorkspaceLayout:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Race")).layout
    page = layout.bundle_dir / f"{ITEM}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: Feature\ntitle: Race\ndescription: d\nstatus: stable\nwork_status: {work_status}\n{owner}"
        "phase: execute\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\naffects:\n- packages/a\n---\n\n"
        "## Summary\nd\n\n## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="",
    )
    (layout.bundle_dir / ITEM / "references").mkdir(parents=True, exist_ok=True)
    ledger = ledger_ref(ITEM)
    ledger.path(layout.bundle_dir).write_text("", encoding="utf-8", newline="")
    document = load(page)
    upsert(document, ledger, title="Decisions")
    page.write_text(document.serialize(), encoding="utf-8", newline="")
    return layout


def _pause(monkeypatch: pytest.MonkeyPatch, module: ModuleType, name: str) -> tuple[threading.Event, threading.Event]:
    entered, release = threading.Event(), threading.Event()
    original = getattr(module, name)

    def paused(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(WAIT), "test never released the paused writer"
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, paused)
    return entered, release


def _record(layout: WorkspaceLayout, phase: str = "execute") -> Callable[[], placement.PlacementRecord]:
    return lambda: placement.run_record_placement(
        layout, ITEM, root=ITEM, phase=phase, worktree=WT, branch=BR, today=TODAY, dry_run=False
    )


def _advance(layout: WorkspaceLayout, tmp_path: Path) -> Callable[[], stage.StageAdvance]:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir(exist_ok=True)
    return lambda: stage.run_stage_advance(
        layout, ITEM, today=TODAY, repo=not_a_repo, infer_worktree=False, dry_run=False
    )


def _file_skip(layout: WorkspaceLayout) -> Callable[[], work.DecisionCommandResult]:
    return lambda: work.run_decision_add(
        layout, ITEM, question="Stop?", hold="skip", phase="execute", on=TODAY, decided_by="coordinator", dry_run=False
    )


def _join_all(*writers: _Run) -> None:
    for writer in writers:
        if writer.ident is not None:
            writer.join(WAIT)
    assert not any(writer.is_alive() for writer in writers), "writer did not exit after release"


def test_record_wins_and_the_waiting_advance_keeps_the_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    page = layout.bundle_dir / f"{ITEM}.md"
    entered, release = _pause(monkeypatch, placement, "apply_mutation")
    recording, advancing = _Run(_record(layout)), _Run(_advance(layout, tmp_path))
    try:
        recording.start()
        assert entered.wait(WAIT)
        before = page.read_bytes()
        advancing.start()
        advancing.join(timeout=0.5)
        assert advancing.is_alive(), "advance must wait on the decision owner's lock"
        assert page.read_bytes() == before
    finally:
        release.set()
        _join_all(recording, advancing)
    assert recording.error is None and advancing.error is None
    assert recording.result.written
    assert advancing.result.outcome.plan.refusal is None
    written = load(page).fm_data()
    assert (written["phase"], written["worktree"], written["branch"]) == ("finish", WT, BR)


def test_advance_wins_and_the_stale_record_refuses_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    page = layout.bundle_dir / f"{ITEM}.md"
    entered, release = _pause(monkeypatch, stage, "apply_mutation")
    advancing, recording = _Run(_advance(layout, tmp_path)), _Run(_record(layout))
    try:
        advancing.start()
        assert entered.wait(WAIT)
        recording.start()
        recording.join(timeout=0.5)
        record_waited = recording.is_alive()
    finally:
        release.set()
        _join_all(advancing, recording)
    assert advancing.error is None and recording.error is None
    assert record_waited, "record must wait on the decision owner's lock"
    assert recording.result.plan.refusal == "phase-mismatch"
    assert recording.result.plan.current_phase == "finish"
    assert recording.result.application is None
    written = load(page).fm_data()
    assert written["phase"] == "finish"
    assert written.get("worktree") is None and written.get("branch") is None


def test_a_same_phase_execute_start_stays_recordable(tmp_path: Path) -> None:
    layout = _layout(tmp_path, work_status="accepted", owner="")
    started = stage.run_stage_advance(layout, ITEM, today=TODAY, owner="pat", infer_worktree=False, dry_run=False)
    assert started.outcome.plan.refusal is None
    assert load(layout.bundle_dir / f"{ITEM}.md").fm_data()["work_status"] == "in-progress"

    assert _record(layout)().written


def test_hold_filing_and_record_both_land_in_either_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    ledger = ledger_ref(ITEM).path(layout.bundle_dir)
    entered, release = _pause(monkeypatch, placement, "apply_mutation")
    recording, filing = _Run(_record(layout)), _Run(_file_skip(layout))
    try:
        recording.start()
        assert entered.wait(WAIT)
        filing.start()
        filing.join(timeout=0.5)
        assert filing.is_alive(), "hold filing must wait on the decision owner's lock"
    finally:
        release.set()
        _join_all(recording, filing)
    assert recording.error is None and filing.error is None
    assert recording.result.written
    assert filing.result.application is not None and filing.result.application.ok
    assert filing.result.plan.primary.id in ledger.read_text(encoding="utf-8")
    written = load(layout.bundle_dir / f"{ITEM}.md").fm_data()
    assert (written["phase"], written["work_status"], written["worktree"]) == ("execute", "in-progress", WT)


def test_a_record_after_a_filed_hold_leaves_the_ledger_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    ledger = ledger_ref(ITEM).path(layout.bundle_dir)
    entered, release = _pause(monkeypatch, work, "_apply_decision")
    filing, recording = _Run(_file_skip(layout)), _Run(_record(layout))
    try:
        filing.start()
        assert entered.wait(WAIT)
        recording.start()
        recording.join(timeout=0.5)
        assert recording.is_alive(), "record must wait on the decision owner's lock"
    finally:
        release.set()
        _join_all(filing, recording)
    assert filing.error is None and recording.error is None
    assert filing.result.application is not None and filing.result.application.ok
    assert recording.result.written
    assert ledger.read_text(encoding="utf-8").count(filing.result.plan.primary.id) == 1
    assert stage.run_stage_advance(layout, ITEM, today=TODAY, dry_run=True).outcome.plan.refusal == "blocked"


@pytest.mark.skipif(sys.platform == "win32", reason="probes the lock with fcntl.flock(LOCK_NB)")
def test_record_apply_runs_while_the_owner_lock_is_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl  # POSIX-only

    layout = _layout(tmp_path)
    lock = owners.decision_lock_path(layout, ITEM)
    lock.parent.mkdir(parents=True, exist_ok=True)
    # The recorder intentionally exposes this patch seam without re-exporting it.
    original = placement.apply_mutation  # type: ignore[attr-defined]
    observed: list[bool] = []

    def probe(*args: Any, **kwargs: Any) -> Any:
        def attempt() -> None:
            if sys.platform != "win32":  # Make the skip visible to mypy too.
                descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    observed.append(False)
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except BlockingIOError:
                    observed.append(True)
                finally:
                    os.close(descriptor)

        prober = _Run(attempt)
        prober.start()
        prober.join(WAIT)
        assert not prober.is_alive() and prober.error is None
        return original(*args, **kwargs)

    monkeypatch.setattr(placement, "apply_mutation", probe)
    assert _record(layout)().written
    assert observed == [True]


def test_guarded_preparation_waits_for_winning_stamp_and_refuses_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    expected = placement.preparation_guard(layout, ITEM)
    entered, release = _pause(monkeypatch, placement, "apply_mutation")
    first = _Run(_record(layout))
    second = _Run(
        lambda: placement.run_record_placement(
            layout,
            ITEM,
            root=ITEM,
            phase="execute",
            worktree=WT,
            branch="other",
            today=TODAY,
            dry_run=False,
            expected_preparation=expected,
        )
    )
    try:
        first.start()
        assert entered.wait(WAIT)
        second.start()
        second.join(timeout=0.5)
        assert second.is_alive(), "preparation record must wait for the placement lock"
    finally:
        release.set()
        _join_all(first, second)
    assert first.error is None and first.result.written
    assert isinstance(second.error, placement.WorkspaceError)
    assert "preparation changed" in str(second.error)
    assert load(layout.bundle_dir / f"{ITEM}.md").fm_data()["branch"] == BR


@pytest.mark.parametrize("changed", ["ancestor", "manifest"])
def test_preparation_read_set_is_rechecked_after_waiting_for_bundle_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    from graph_works_core.workspace import transactions
    from test_record_placement import EPIC, NESTED, _vault

    layout = _vault(tmp_path)
    expected = placement.preparation_guard(layout, NESTED)
    entered, release = _pause(monkeypatch, transactions, "_bundle_root_lock")
    recording = _Run(
        lambda: placement.run_record_placement(
            layout,
            NESTED,
            root=NESTED,
            phase="plan",
            worktree=WT,
            branch=BR,
            today=TODAY,
            dry_run=False,
            expected_preparation=expected,
        )
    )
    target = layout.bundle_dir / f"{NESTED}.md"
    before = target.read_bytes()
    try:
        recording.start()
        assert entered.wait(WAIT)
        if changed == "ancestor":
            page = layout.bundle_dir / f"{EPIC}.md"
            document = load(page)
            document.set("repo", "other")
            page.write_text(document.serialize(), encoding="utf-8", newline="")
        else:
            with layout.manifest_path.open("a", encoding="utf-8", newline="") as stream:
                stream.write("\n# repository configuration changed\n")
    finally:
        release.set()
        _join_all(recording)
    assert recording.error is None
    assert not recording.result.written
    assert "preparation changed" in str(recording.result.application.failures)
    assert target.read_bytes() == before

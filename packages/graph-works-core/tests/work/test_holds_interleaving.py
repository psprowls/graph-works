"""Hold filing and stage advance serialize on the decision owner's lock.

No sleeps synchronize anything: `threading.Event`s pause each writer at the
call it already makes after its in-lock read and before releasing the lock.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.work import commands as work
from graph_works_core.workspace import decision_owner as owners
from okf_io import load
from work_tracker_okf.decisions import ledger_ref
from work_tracker_okf.sources import upsert

TODAY = date(2026, 9, 13)
ITEM = "work/feature-race"
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
        except BaseException as exc:  # surfaced by the test
            self.error = exc


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Race")).layout
    page = layout.bundle_dir / f"{ITEM}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Feature\ntitle: Race\ndescription: d\nstatus: stable\nwork_status: in-progress\nowner: pat\n"
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


def _pause(monkeypatch, module, name: str) -> tuple[threading.Event, threading.Event]:
    entered, release = threading.Event(), threading.Event()
    original = getattr(module, name)

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(WAIT), "test never released the paused writer"
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, paused)
    return entered, release


def _advance(layout, tmp_path: Path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir(exist_ok=True)
    return lambda: stage.run_stage_advance(layout, ITEM, today=TODAY, repo=not_a_repo, dry_run=False)


def _file_skip(layout):
    return lambda: work.run_decision_add(
        layout, ITEM, question="Stop?", hold="skip", phase="execute", on=TODAY, decided_by="coordinator", dry_run=False
    )


def test_filing_wins_and_the_waiting_advance_is_refused(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    page = layout.bundle_dir / f"{ITEM}.md"
    entered, release = _pause(monkeypatch, work, "_apply_decision")
    filing = _Run(_file_skip(layout))
    advancing = _Run(_advance(layout, tmp_path))
    try:
        filing.start()
        assert entered.wait(WAIT)
        before = page.read_bytes()
        advancing.start()
        advancing.join(timeout=0.5)
        assert advancing.is_alive(), "advance must wait on the decision owner's lock"
        assert page.read_bytes() == before
    finally:
        release.set()
        for writer in (filing, advancing):
            if writer.ident is not None:
                writer.join(WAIT)
        assert not filing.is_alive() and not advancing.is_alive(), "writer did not exit after release"
    assert filing.error is None and advancing.error is None
    decision_id = filing.result.plan.primary.id
    assert filing.result.application.ok
    assert advancing.result.outcome.plan.refusal == "blocked"
    assert f"open decision {decision_id} (skip)" in advancing.result.outcome.plan.detail
    assert load(page).fm_data()["phase"] == "execute"


def test_advance_wins_and_the_waiting_filing_is_refused(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    ledger = layout.bundle_dir / ITEM / "references" / "00-decisions.md"
    entered, release = _pause(monkeypatch, stage, "apply_mutation")
    advancing = _Run(_advance(layout, tmp_path))
    before = ledger.read_bytes()
    filing = _Run(_file_skip(layout))
    try:
        advancing.start()
        assert entered.wait(WAIT)
        filing.start()
        filing.join(timeout=0.5)
        filing_waited = filing.is_alive()
        ledger_while_paused = ledger.read_bytes()
    finally:
        release.set()
        for writer in (advancing, filing):
            if writer.ident is not None:
                writer.join(WAIT)
        assert not filing.is_alive() and not advancing.is_alive(), "writer did not exit after release"
    assert advancing.error is None and filing.error is None
    assert filing.result.plan.refusal == "hold-phase-mismatch", (
        "filing must reject the stale execute hold after advance wins",
        ledger.read_text(encoding="utf-8"),
        advancing.result.application,
    )
    assert advancing.result.application is not None and advancing.result.application.ok
    assert load(layout.bundle_dir / f"{ITEM}.md").fm_data()["phase"] == "finish"
    assert filing_waited, "filing must wait on the decision owner's lock"
    assert ledger_while_paused == before
    assert "finish" in filing.result.plan.detail
    assert ledger.read_bytes() == before


@pytest.mark.skipif(sys.platform == "win32", reason="probes the lock with fcntl.flock(LOCK_NB)")
def test_apply_mutation_runs_while_the_owner_lock_is_held(tmp_path: Path, monkeypatch) -> None:
    import fcntl  # POSIX-only

    layout = _layout(tmp_path)
    lock = owners.decision_lock_path(layout, ITEM)
    lock.parent.mkdir(parents=True, exist_ok=True)
    original = stage.apply_mutation
    observed: list[bool] = []

    def probe(*args, **kwargs):
        def attempt() -> None:
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
        assert not prober.is_alive()
        assert prober.error is None
        return original(*args, **kwargs)

    monkeypatch.setattr(stage, "apply_mutation", probe)
    result = _advance(layout, tmp_path)()
    assert observed == [True]
    assert result.application is not None and result.application.ok


def test_a_dry_run_advance_reports_the_hold_and_takes_no_lock(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _file_skip(layout)()
    monkeypatch.setattr(owners, "locked_decision_owner", lambda *a, **k: pytest.fail("dry run must not lock"))
    monkeypatch.setattr(stage, "locked_decision_owner", lambda *a, **k: pytest.fail("dry run must not lock"))
    result = stage.run_stage_advance(layout, ITEM, today=TODAY, dry_run=True)
    assert result.outcome.plan.refusal == "blocked"


def test_answering_the_hold_lets_the_same_advance_succeed(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    filed = _file_skip(layout)()
    assert _advance(layout, tmp_path)().outcome.plan.refusal == "blocked"
    work.run_decision_answer(
        layout, ITEM, filed.plan.primary.id, answer="go", on=TODAY, decided_by="user", dry_run=False
    )
    advanced = _advance(layout, tmp_path)()
    assert advanced.outcome.plan.refusal is None
    assert advanced.application is not None and advanced.application.ok
    assert load(layout.bundle_dir / f"{ITEM}.md").fm_data()["phase"] == "finish"


@pytest.mark.parametrize("unreadable", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_missing_or_unreadable_item_returns_refusal_without_locking(tmp_path: Path, monkeypatch, unreadable, dry_run):
    layout = _layout(tmp_path)
    page = layout.bundle_dir / f"{ITEM}.md"
    if unreadable:
        page.write_bytes(b"\xff")
    else:
        page.unlink()
    monkeypatch.setattr(stage, "locked_decision_owner", lambda *a, **k: pytest.fail("missing item must not lock"))
    result = stage.run_stage_advance(layout, ITEM, today=TODAY, dry_run=dry_run)
    assert result.outcome.plan.refusal == ("unreadable-member" if unreadable else "unknown-path")
    assert result.application is None

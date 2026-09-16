"""The epic design's child-3 fixture: park and skip across ledgers."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.work import commands as work
from graph_works_core.workspace import decision_owner
from okf_io import load_bundle
from test_orchestrate_shell import _declare_repo, _workspace, _write
from work_tracker_okf.items import IGNORE, load_items

TODAY = date(2026, 9, 13)
EPIC = "work/epic-h"
PARKED = f"{EPIC}/children/feature-parked"
SKIPPED = f"{EPIC}/children/bug-skipped"
SIBLING = f"{EPIC}/children/bug-sibling"
DEPENDENT = f"{EPIC}/children/bug-dependent"


def _ledger(layout, owner: str, text: str) -> None:
    ledger = layout.bundle_dir / f"{owner}/references/00-decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(text, encoding="utf-8", newline="")


def _dependent(layout) -> None:
    _write(layout, DEPENDENT, type="Bug", phase="execute", work_status="accepted", affects=("packages/d",))
    page = layout.bundle_dir / f"{DEPENDENT}.md"
    text = page.read_text(encoding="utf-8")
    page.write_text(
        text.replace(
            "affects:", f"depends_on:\n  - path: {SKIPPED}\n    blocks: execute\n    needs: resolved\naffects:", 1
        ),
        encoding="utf-8",
        newline="",
    )


def _anchor(layout) -> None:
    checkout = layout.root / "checkout"
    checkout.mkdir()
    page = layout.bundle_dir / f"{EPIC}.md"
    text = page.read_text(encoding="utf-8")
    page.write_text(
        text.replace("affects:", f"worktree: {checkout}\nbranch: feature/holds\naffects:", 1),
        encoding="utf-8",
        newline="",
    )


def _fixture(tmp_path: Path):
    layout = _workspace(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute", work_status="in-progress")
    _anchor(layout)
    _write(layout, PARKED, phase="execute", work_status="accepted")
    _write(layout, SKIPPED, type="Bug", phase="execute", work_status="accepted")
    _write(layout, SIBLING, type="Bug", phase="plan")
    _dependent(layout)
    _ledger(
        layout,
        PARKED,
        f"## D-001 — resume?\nstatus: open\naffects: [{PARKED}]\nhold: park\nphase: execute\n"
        f"checkpoint: /{PARKED}/references/03-execute-checkpoint-D-001.md\n",
    )
    _ledger(layout, EPIC, f"## D-001 — stop?\nstatus: open\naffects: [{SKIPPED}]\nhold: skip\nphase: execute\n")
    return layout


def test_held_items_are_blocked_reserve_nothing_and_are_reported(tmp_path: Path) -> None:
    layout = _fixture(tmp_path)
    for path in (PARKED, SKIPPED):
        assert work.run_next(layout, path).route.dispatch is None
    result = orchestrate.run_orchestrate(layout, EPIC)
    dispatched = {dispatch.slug for dispatch in result.dispatches}
    blocked = {item.path: item.kind for item in result.blocked}
    assert PARKED not in dispatched and SKIPPED not in dispatched
    assert blocked[PARKED] == "decisions" and blocked[SKIPPED] == "decisions"
    assert SIBLING in dispatched  # shares packages/a with both held items
    assert blocked[DEPENDENT] == "deps"
    reported = {(hold.path, hold.decision.id, hold.decision.hold, hold.owner_path) for hold in result.holds}
    assert reported == {(PARKED, "D-001", "park", PARKED), (SKIPPED, "D-001", "skip", EPIC)}
    assert all(hold.ledger_path.endswith("references/00-decisions.md") for hold in result.holds)


def test_answering_each_hold_releases_the_item_on_the_next_plan(tmp_path: Path, monkeypatch) -> None:
    apply_init(plan_init(tmp_path, today=TODAY, topic="Holds"))
    layout = _fixture(tmp_path)
    for path in (PARKED, SKIPPED):
        answer = work.run_decision_answer(
            layout, path, "D-001", answer="go", on=TODAY, decided_by="user", dry_run=False
        )
        assert answer.application is not None and answer.application.ok
    _declare_repo(monkeypatch, tmp_path)
    result = orchestrate.run_orchestrate(layout, EPIC)
    assert result.holds == ()
    assert {item.path: item.kind for item in result.blocked}.get(PARKED) != "decisions"
    assert {item.path: item.kind for item in result.blocked}.get(SKIPPED) != "decisions"


def test_a_held_epic_still_plans_its_open_children(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute", work_status="in-progress")
    _anchor(layout)
    _write(layout, SIBLING, type="Bug", phase="plan")
    _ledger(layout, EPIC, f"## D-001 — pause epic?\nstatus: open\naffects: [{EPIC}]\nhold: skip\nphase: execute\n")
    result = orchestrate.run_orchestrate(layout, EPIC)
    assert SIBLING in {dispatch.slug for dispatch in result.dispatches}
    assert [hold.path for hold in result.holds] == [EPIC]


def test_open_holds_reports_every_decision_sorted_and_reads_each_owner_once(tmp_path: Path, monkeypatch) -> None:
    layout = _fixture(tmp_path)
    _ledger(
        layout,
        EPIC,
        f"## D-010 — later question?\nstatus: open\naffects: [{SKIPPED}, {SIBLING}]\n"
        f"## D-002 — earlier question?\nstatus: open\naffects: [{SKIPPED}]\n"
        f"## D-001 — settled?\nstatus: answered\naffects: [{SKIPPED}]\n",
    )
    items = tuple(load_items(load_bundle(layout.bundle_dir, ignore=IGNORE)))
    reads: list[Path] = []
    original = decision_owner._decisions.load

    def counted_load(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(decision_owner._decisions, "load", counted_load)
    reports = decision_owner.open_holds(
        items, layout.bundle_dir, [SKIPPED, PARKED, SIBLING, SKIPPED, "work/bug-missing"]
    )
    assert [(report.path, report.decision.id) for report in reports] == [
        (SIBLING, "D-010"),
        (SKIPPED, "D-002"),
        (SKIPPED, "D-010"),
        (PARKED, "D-001"),
    ]
    assert set(reads) == {
        layout.bundle_dir / f"{EPIC}/references/00-decisions.md",
        layout.bundle_dir / f"{PARKED}/references/00-decisions.md",
    }
    assert len(reads) == 2
    assert all(isinstance(report, orchestrate.HoldReport) for report in reports)


def test_lone_root_reports_questions_but_excludes_holds_outside_its_subtree(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    root = "work/bug-lone"
    other = "work/feature-other"
    for path, type_ in ((root, "Bug"), (other, "Feature")):
        _write(layout, path, type=type_, phase="design")
        _ledger(layout, path, f"## D-001 — question?\nstatus: open\naffects: [{path}]\n")
    result = orchestrate.run_orchestrate(layout, root)
    assert [(hold.path, hold.owner_path, hold.decision.hold) for hold in result.holds] == [(root, root, None)]
    assert result.open_decisions == (result.holds[0].decision,)
    assert result.assumed_decisions == ()
    assert orchestrate.OrchestrateResult(plan=result.plan).holds == ()

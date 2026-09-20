"""`run_open_decisions`: every open entry in every active owner's ledger."""

from __future__ import annotations

from pathlib import Path

from graph_works_core.work import commands as work
from test_run_next import CHILD, EPIC, _layout, _write

LONE = "work/feature-lone"
ARCHIVED = "work/_archive/epic-old"


def _ledger(layout, owner: str, text: str) -> Path:
    ledger = layout.bundle_dir / f"{owner}/references/00-decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(text, encoding="utf-8", newline="")
    return ledger


def test_open_entries_sorted_by_owner_then_number_with_held_items(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD, type="Bug", phase="plan")
    _write(layout, LONE)
    epic_ledger = _ledger(
        layout,
        EPIC,
        f"## D-003 — later\nstatus: open\naffects: [{CHILD}]\n\n"
        f"## D-001 — mixed\nstatus: open\naffects: [packages/a, {CHILD}, {LONE}]\n\n"
        f"## D-002 — done\nstatus: answered\naffects: [{CHILD}]\n\n"
        f"## D-004 — replaced\nstatus: superseded\naffects: [{CHILD}]\n",
    )
    _ledger(layout, LONE, f"## D-001 — mine\nstatus: open\naffects: [{LONE}]\n")

    found = work.run_open_decisions(layout)

    assert [(record.owner_path, record.decision.id) for record in found] == [
        (EPIC, "D-001"),
        (EPIC, "D-003"),
        (LONE, "D-001"),
    ]
    assert found[0].ledger == epic_ledger
    # A code path is never held; LONE is its own owner, so EPIC's ledger does not hold it.
    assert found[0].held == (CHILD,)
    assert found[1].held == (CHILD,)
    assert found[2].held == (LONE,)


def test_a_missing_ledger_reads_as_empty(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, LONE)

    assert work.run_open_decisions(layout) == ()


def test_an_archived_items_ledger_is_never_read(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _write(layout, LONE)
    _write(layout, ARCHIVED, type="Epic", phase="execute")
    archived_ledger = _ledger(layout, ARCHIVED, f"## D-001 — old\nstatus: open\naffects: [{ARCHIVED}]\n")
    read: list[Path] = []
    original = work._decisions.load
    monkeypatch.setattr(work._decisions, "load", lambda ledger: read.append(ledger) or original(ledger))

    assert work.run_open_decisions(layout) == ()
    assert archived_ledger not in read
    assert len(read) == 1  # LONE's (absent) ledger, read once

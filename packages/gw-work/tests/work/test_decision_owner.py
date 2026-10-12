"""Hold resolution through the shared decision-owner module."""

from __future__ import annotations

import itertools
from datetime import date
from pathlib import Path

import pytest
from gw_kernel import apply_init, plan_init
from gw_pipeline import decision_owner as owners
from gw_work import commands as work

TODAY = date(2026, 9, 13)
EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/bug-a"
LONE = "work/bug-lone"


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=TODAY, topic="Holds")).layout


def _page(layout, path: str, type: str, phase: str = "execute") -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\nwork_status: in-progress\n"
        f"owner: pat\nphase: {phase}\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n"
        "affects:\n- packages/a\n---\n\n## Summary\nd\n",
        encoding="utf-8",
        newline="",
    )


def _ledger(layout, owner: str, text: str) -> None:
    ledger = layout.bundle_dir / f"{owner}/references/00-decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(text, encoding="utf-8", newline="")


def test_decision_add_refuses_after_repeated_owner_changes(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _page(layout, EPIC, "Epic")
    _page(layout, CHILD, "Bug")
    calls = itertools.cycle([EPIC, CHILD])
    seen: list[str] = []

    def flapping(items, path):
        owner = next(calls)
        seen.append(owner)
        return owner

    monkeypatch.setattr(owners, "decision_owner", flapping)
    with pytest.raises(ValueError, match="changed during locking"):
        work.run_decision_add(
            layout,
            CHILD,
            question="q",
            affects=(CHILD,),
            on=TODAY,
            decided_by="pat",
            dry_run=False,
        )
    assert not (layout.bundle_dir / f"{EPIC}/references/00-decisions.md").exists()
    assert not (layout.bundle_dir / f"{CHILD}/references/00-decisions.md").exists()

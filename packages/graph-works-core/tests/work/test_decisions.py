"""Decision-ledger CRUD: `run_decision_add` / `run_decision_answer` /
`run_decision_supersede`, each resolving the ledger from the item's own slug
via its nearest epic ancestor."""

from __future__ import annotations

from datetime import date

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work

TODAY = date(2026, 8, 17)

_ITEM = """---
type: {type}
title: {slug}
description: d
status: stable
workflow_status: open
phase: {phase}
effort: medium
opened: 2026-08-01
updated: 2026-08-01
affects:
- packages/a
{extra}---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""


def _workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write_item(layout, slug, *, type="Feature", phase="design", extra=""):
    (layout.bundle_dir / "work" / f"{slug}.md").write_text(
        _ITEM.format(type=type, slug=slug, phase=phase, extra=extra), encoding="utf-8"
    )


def _seeded(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "2026-08-01-epic-x", type="Epic", phase="execute")
    _write_item(layout, "2026-08-02-feature-a", extra="parent: 2026-08-01-epic-x\n")
    return layout


def test_add_appends_to_the_owning_epics_ledger(tmp_path):
    layout = _seeded(tmp_path)
    decision = work.run_decision_add(
        layout, "2026-08-02-feature-a", question="Which store?", status="open", affects=["2026-08-02-feature-a"]
    )
    assert decision.id == "D-001"
    ledger = layout.bundle_dir / "work" / "2026-08-01-epic-x" / "references" / "00-decisions.md"
    assert "D-001" in ledger.read_text(encoding="utf-8")


def test_add_with_no_epic_ancestor_raises(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "2026-08-01-feature-lone")
    with pytest.raises(ValueError, match="no epic ancestor") as excinfo:
        work.run_decision_add(layout, "2026-08-01-feature-lone", question="q", status="open")
    assert "2026-08-01-feature-lone" in str(excinfo.value)


def test_answer_updates_the_entry_in_place(tmp_path):
    layout = _seeded(tmp_path)
    work.run_decision_add(layout, "2026-08-02-feature-a", question="Which store?", status="open")
    updated = work.run_decision_answer(
        layout, "2026-08-02-feature-a", "D-001", status="answered", decided="2026-08-17 by user"
    )
    assert updated.status == "answered"
    assert updated.decided == "2026-08-17 by user"
    # `question` was never passed -- UNSET, not None -- so it must survive
    # untouched. This is the behavior the whole `Unset` sentinel exists for.
    assert updated.question == "Which store?"


def test_supersede_retires_the_old_entry_and_appends_a_replacement(tmp_path):
    layout = _seeded(tmp_path)
    work.run_decision_add(layout, "2026-08-02-feature-a", question="Which store?", status="open")
    retired, replacement = work.run_decision_supersede(
        layout, "2026-08-02-feature-a", "D-001", question="Which store, revised?", prose="sqlite"
    )
    assert retired.status == "superseded"
    assert replacement.supersedes == "D-001"
    assert replacement.status == "answered"

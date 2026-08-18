"""`run_next`: routes *slug*, resolving `has_open_decision` per-slug -- the
case `work-tracker-okf/cli.py`'s `next_stage` could never report."""

from __future__ import annotations

from datetime import date

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


def _write_item(layout, slug, *, type="Feature", phase="plan", extra=""):
    (layout.bundle_dir / "work" / f"{slug}.md").write_text(
        _ITEM.format(type=type, slug=slug, phase=phase, extra=extra), encoding="utf-8"
    )


def _ledger(layout, epic_slug):
    path = layout.bundle_dir / "work" / epic_slug / "references" / "00-decisions.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_unknown_slug_returns_none(tmp_path):
    layout = _workspace(tmp_path)
    assert work.run_next(layout, "no-such-slug") is None


def test_a_lone_item_routes_with_no_open_decision(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "2026-08-01-feature-a", phase="plan")
    result = work.run_next(layout, "2026-08-01-feature-a")
    assert result is not None
    assert result.state.has_open_decision is False
    assert result.result.dispatch is not None


def test_an_open_decision_affecting_the_slug_blocks_it(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "2026-08-01-epic-x", type="Epic", phase="execute")
    _write_item(layout, "2026-08-02-feature-a", phase="design", extra="parent: 2026-08-01-epic-x\n")
    _ledger(layout, "2026-08-01-epic-x").write_text(
        "# Decisions\n\n## D-001 — question\nstatus: open\naffects: [2026-08-02-feature-a]\n\nprose\n",
        encoding="utf-8",
    )
    result = work.run_next(layout, "2026-08-02-feature-a")
    assert result is not None
    assert result.state.has_open_decision is True
    assert result.result.dispatch is None
    assert "open decision" in result.result.blockers[0]


def test_an_open_decision_not_naming_the_slug_does_not_block_it(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "2026-08-01-epic-x", type="Epic", phase="execute")
    _write_item(layout, "2026-08-02-feature-a", phase="design", extra="parent: 2026-08-01-epic-x\n")
    _ledger(layout, "2026-08-01-epic-x").write_text(
        "# Decisions\n\n## D-001 — question\nstatus: open\naffects: [some-other-slug]\n\nprose\n",
        encoding="utf-8",
    )
    result = work.run_next(layout, "2026-08-02-feature-a")
    assert result is not None
    assert result.state.has_open_decision is False
    assert result.result.dispatch is not None

"""`run_regen_index`: `update_index` over `work/` alone, unwrapped to one
`IndexUpdate`."""

from __future__ import annotations

from datetime import date

from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work

TODAY = date(2026, 8, 17)

_ITEM = """---
type: Feature
title: {slug}
description: d
status: stable
workflow_status: open
phase: plan
effort: medium
opened: 2026-08-01
updated: 2026-08-01
affects:
- packages/a
---

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


def test_a_dry_run_writes_nothing(tmp_path):
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "work" / "2026-08-01-feature-a.md").write_text(
        _ITEM.format(slug="2026-08-01-feature-a"), encoding="utf-8"
    )
    index_path = layout.bundle_dir / "work" / "index.md"
    before_exists = index_path.exists()
    update = work.run_regen_index(layout)
    assert update.changed
    assert index_path.exists() == before_exists


def test_a_real_run_writes_a_missing_index(tmp_path):
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "work" / "2026-08-01-feature-a.md").write_text(
        _ITEM.format(slug="2026-08-01-feature-a"), encoding="utf-8"
    )
    update = work.run_regen_index(layout, dry_run=False)
    assert update.changed
    index_path = layout.bundle_dir / "work" / "index.md"
    assert index_path.is_file()
    assert "2026-08-01-feature-a" in index_path.read_text(encoding="utf-8")

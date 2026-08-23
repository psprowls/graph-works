"""`run_status`: `projection.rollup` + `projection.select_resume`, read
together."""

from __future__ import annotations

from datetime import date

from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from okf_io import load

TODAY = date(2026, 8, 17)

_ITEM = """---
type: Feature
title: {slug}
description: d
status: stable
work_status: open
phase: execute
effort: medium
opened: 2026-08-01
updated: {updated}
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


def _write_item(layout, slug, *, updated="2026-08-01"):
    (layout.bundle_dir / "work" / f"{slug}.md").write_text(_ITEM.format(slug=slug, updated=updated), encoding="utf-8")


def test_the_rollup_counts_active_items(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "feature-a")
    _write_item(layout, "feature-b")
    report = work.run_status(layout)
    assert report.rollup.total == 2
    assert dict(report.rollup.by_type) == {"Feature": 2}


def test_resume_picks_the_most_recently_updated_item(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "feature-a", updated="2026-08-01")
    _write_item(layout, "feature-b", updated="2026-08-10")
    report = work.run_status(layout)
    assert report.resume is not None
    assert report.resume.primary.path == "work/feature-b"


def test_status_rollup_accepts_structured_dependency_items(tmp_path) -> None:
    layout = _workspace(tmp_path)
    _write_item(layout, "feature-a")
    _write_item(layout, "feature-b")
    document = load(layout.bundle_dir / "work/feature-b.md")
    document.set(
        "depends_on",
        [{"path": "feature-a", "blocks": "plan", "needs": "design"}],
    )
    document.save()
    assert work.run_status(layout).rollup.total == 2

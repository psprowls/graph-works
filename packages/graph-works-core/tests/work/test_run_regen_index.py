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
work_status: open
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


def _write_epic(layout, path: str) -> None:
    slug = path.rsplit("/", 1)[-1]
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(_ITEM.replace("type: Feature", "type: Epic").format(slug=slug), encoding="utf-8")


def test_a_dry_run_writes_nothing(tmp_path):
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "work" / "2026-08-01-feature-a.md").write_text(
        _ITEM.format(slug="2026-08-01-feature-a"), encoding="utf-8"
    )
    index_path = layout.bundle_dir / "work" / "index.md"
    before_exists = index_path.exists()
    result = work.run_regen_indexes(layout)
    assert any(plan.changed for plan in result.plans)
    assert index_path.exists() == before_exists


def test_a_real_run_writes_a_missing_index(tmp_path):
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "work" / "2026-08-01-feature-a.md").write_text(
        _ITEM.format(slug="2026-08-01-feature-a"), encoding="utf-8"
    )
    result = work.run_regen_indexes(layout, dry_run=False)
    assert result.application is not None and result.application.ok
    index_path = layout.bundle_dir / "work" / "index.md"
    assert index_path.is_file()
    assert "2026-08-01-feature-a" in index_path.read_text(encoding="utf-8")


def test_run_regen_indexes_returns_every_required_lane(tmp_path):
    layout = _workspace(tmp_path)
    release = "work/release-cutover"
    (layout.bundle_dir / f"{release}.md").write_text(
        _ITEM.replace("type: Feature", "type: Release").format(slug="release-cutover"),
        encoding="utf-8",
    )

    result = work.run_regen_indexes(layout, dry_run=True)

    assert {plan.lane for plan in result.plans} >= {
        "work",
        f"{release}/children",
    }


def test_regen_refuses_an_index_changed_after_domain_planning(tmp_path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    page = layout.bundle_dir / "work/feature-a.md"
    page.write_text(_ITEM.format(slug="feature-a"), encoding="utf-8")
    original = work.plan_indexes
    raced = layout.bundle_dir / "work/index.md"
    external = "# maintained elsewhere\n"

    def inject_after_planning(*args, **kwargs):
        plans = original(*args, **kwargs)
        raced.write_text(external, encoding="utf-8")
        return plans

    monkeypatch.setattr(work, "plan_indexes", inject_after_planning)
    result = work.run_regen_indexes(layout, dry_run=False)
    assert result.application is not None and not result.application.ok
    assert raced.read_text(encoding="utf-8") == external


def test_regen_refuses_an_absent_lane_created_after_domain_planning(tmp_path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    archive_lane = layout.bundle_dir / "work/_archive"
    assert not archive_lane.exists()
    original = work.plan_indexes

    def inject_after_planning(*args, **kwargs):
        plans = original(*args, **kwargs)
        archive_lane.mkdir(parents=True)
        (archive_lane / "external.bin").write_bytes(b"external owner")
        return plans

    monkeypatch.setattr(work, "plan_indexes", inject_after_planning)
    result = work.run_regen_indexes(layout, dry_run=False)
    assert result.application is not None and not result.application.ok
    assert (archive_lane / "external.bin").read_bytes() == b"external owner"
    assert not (archive_lane / "index.md").exists()


def test_regen_index_does_not_create_an_archive_lane_for_a_parent_without_archived_children(
    tmp_path,
) -> None:
    layout = _workspace(tmp_path)
    _write_epic(layout, "work/epic-new")

    result = work.run_regen_indexes(layout, dry_run=False)

    planned = {plan.lane for plan in result.plans}
    assert "work/epic-new/children" in planned
    assert "work/epic-new/children/_archive" not in planned
    assert not (layout.bundle_dir / "work/epic-new/children/_archive").exists()

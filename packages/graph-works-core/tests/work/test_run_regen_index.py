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


def test_regen_indexes_migrates_a_legacy_marked_lane_index_in_one_pass(tmp_path) -> None:
    layout = _workspace(tmp_path)
    _write_epic(layout, "work/epic-stays")
    index_path = layout.bundle_dir / "work" / "index.md"
    index_path.write_text(
        "<!-- graph-works:work-items:start -->\n"
        "- [Epic: Stays](epic-stays.md) — open · design\n"
        "- [Epic: Leaves](epic-leaves.md) — open · design\n"
        "<!-- graph-works:work-items:end -->\n",
        encoding="utf-8",
    )

    result = work.run_regen_indexes(layout, dry_run=False)

    assert result.application is not None and result.application.ok
    after = index_path.read_text(encoding="utf-8")
    assert "graph-works:work-items" not in after
    assert "epic-stays.md" in after
    assert "epic-leaves.md" not in after


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


def test_regen_index_strips_legacy_markers_from_a_nested_non_required_lane_in_the_same_run(tmp_path) -> None:
    """An active parent with no archived children no longer requires its
    `children/_archive/` lane, so the reconcile pass never plans that inert
    index. The same run still strips its legacy marker lines -- and only
    those: every other byte, a link bullet included, stays exactly as it was."""
    layout = _workspace(tmp_path)
    _write_epic(layout, "work/epic-active")
    root_index = layout.bundle_dir / "work" / "index.md"
    root_index.write_text(
        "<!-- graph-works:work-items:start -->\n"
        "- [Epic: Active](epic-active.md) — open · plan\n"
        "<!-- graph-works:work-items:end -->\n",
        encoding="utf-8",
    )
    inert_lane = layout.bundle_dir / "work" / "epic-active" / "children" / "_archive"
    inert_lane.mkdir(parents=True)
    inert_index = inert_lane / "index.md"
    inert_index.write_text(
        "# Archived children\n"
        "\n"
        "Nothing has been archived here since the root-only archive policy.\n"
        "\n"
        "<!-- graph-works:work-items:start -->\n"
        "<!-- graph-works:work-items:end -->\n"
        "\n"
        "- [Archive policy](../../../../adrs/archive-policy.md)\n",
        encoding="utf-8",
    )

    result = work.run_regen_indexes(layout, dry_run=False)

    assert result.application is not None and result.application.ok, result.application
    assert "work/epic-active/children/_archive" not in {plan.lane for plan in result.plans}
    assert inert_index.read_text(encoding="utf-8") == (
        "# Archived children\n"
        "\n"
        "Nothing has been archived here since the root-only archive policy.\n"
        "\n"
        "\n"
        "- [Archive policy](../../../../adrs/archive-policy.md)\n"
    )
    assert "graph-works:work-items" not in root_index.read_text(encoding="utf-8")
    assert [plan.lane for plan in result.marker_strips] == ["work/epic-active/children/_archive"]


def test_regen_index_leaves_markers_on_a_non_required_lane_whose_entries_are_stale(tmp_path) -> None:
    """Stripping alone must never write an index the postcondition gate
    would call stale -- that would roll back the whole run. A non-required
    lane whose own entries are out of date keeps its markers, byte for byte,
    and the run says so instead of reconciling entries it was not asked to."""
    layout = _workspace(tmp_path)
    _write_epic(layout, "work/epic-active")
    inert_lane = layout.bundle_dir / "work" / "epic-active" / "children" / "_archive"
    inert_lane.mkdir(parents=True)
    inert_index = inert_lane / "index.md"
    original = (
        "<!-- graph-works:work-items:start -->\n"
        "- [Feature: Gone](feature-gone.md) — resolved · done\n"
        "<!-- graph-works:work-items:end -->\n"
    )
    inert_index.write_text(original, encoding="utf-8")

    result = work.run_regen_indexes(layout, dry_run=False)

    assert result.application is not None and result.application.ok, result.application
    assert inert_index.read_text(encoding="utf-8") == original
    assert result.marker_strips == ()
    assert any("work/epic-active/children/_archive/index.md" in warning for warning in result.mutation.warnings)

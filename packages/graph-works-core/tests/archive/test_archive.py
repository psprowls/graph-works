"""The archive vertical: plan, apply, clear the pointer, log it once.

`provenance.clear_active_work` was implemented, tested and never called --
which is the stale-pointer failure its own docstring names. This module is the
caller, and these are its acceptance properties.
"""

from __future__ import annotations

import json
from datetime import date

from graph_works_core import apply_init, plan_init
from graph_works_core.archive import commands as archive
from graph_works_core.workspace import provenance

TODAY = date(2026, 8, 15)

_DONE = "2026-08-01-epic-feature-finished-thing"
_OPEN = "2026-08-02-epic-feature-ongoing-thing"

_ITEM = """---
type: Feature
title: {slug}
description: d
status: stable
workflow_status: {workflow_status}
phase: {phase}
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
    """A workspace built the way a caller builds one, with two items in it."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Archive")).layout
    work = layout.bundle_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{_DONE}.md").write_text(
        _ITEM.format(slug=_DONE, workflow_status="resolved", phase="done"), encoding="utf-8"
    )
    (work / f"{_OPEN}.md").write_text(
        _ITEM.format(slug=_OPEN, workflow_status="open", phase="execute"), encoding="utf-8"
    )
    return layout


def _pointer(layout):
    return layout.cache_dir / provenance.ACTIVE_WORK_FILENAME


def _page(layout, slug, *, archived=False):
    lane = "work/_archive" if archived else "work"
    return layout.bundle_dir / lane / f"{slug}.md"


def _wiki_page(layout, token, *, archived=False):
    lane, slug = token.split("/", 1)
    dirname = f"{lane}/_archive" if archived else lane
    return layout.bundle_dir / dirname / f"{slug}.md"


def _write_wiki_page(layout, token, title):
    path = _wiki_page(layout, token)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {title}\ndescription: d\n---\n\n## Summary\nd\n", encoding="utf-8")


def test_a_dry_run_moves_nothing_clears_nothing_and_logs_nothing(tmp_path):
    layout = _workspace(tmp_path)
    provenance.write_active_work(layout, _DONE, "execute", updated="2026-08-01")
    before = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")

    run = archive.run_archive(layout, today=TODAY)

    assert run.plan.slugs == (_DONE,)
    assert run.result is None
    assert run.pointer_cleared is False
    assert run.logged is None
    assert _page(layout, _DONE).is_file()
    assert not _page(layout, _DONE, archived=True).exists()
    assert _pointer(layout).is_file()
    assert (layout.bundle_dir / "log.md").read_text(encoding="utf-8") == before


def test_an_applied_archive_moves_the_item_and_clears_its_pointer(tmp_path):
    layout = _workspace(tmp_path)
    provenance.write_active_work(layout, _DONE, "execute", updated="2026-08-01")

    run = archive.run_archive(layout, today=TODAY, dry_run=False)

    assert run.result is not None
    assert run.result.archived == (_DONE,)
    assert not _page(layout, _DONE).exists()
    assert _page(layout, _DONE, archived=True).is_file()
    assert run.pointer_cleared is True
    assert not _pointer(layout).exists()


def test_a_pointer_naming_another_slug_survives(tmp_path):
    layout = _workspace(tmp_path)
    provenance.write_active_work(layout, _OPEN, "execute", updated="2026-08-01")

    run = archive.run_archive(layout, today=TODAY, dry_run=False)

    assert run.result is not None and run.result.archived == (_DONE,)
    assert run.pointer_cleared is False
    assert json.loads(_pointer(layout).read_text(encoding="utf-8"))["slug"] == _OPEN


def test_a_skipped_slug_clears_nothing(tmp_path):
    # The pointer is cleared from `result.archived` -- what actually moved --
    # not from the requested slugs. A refused slug must not clear a live one.
    layout = _workspace(tmp_path)
    provenance.write_active_work(layout, _OPEN, "execute", updated="2026-08-01")

    run = archive.run_archive(layout, [_OPEN], today=TODAY, dry_run=False)

    assert run.result is not None
    assert run.result.archived == ()
    assert [(s.slug, s.reason) for s in run.result.skipped] == [(_OPEN, "not-terminal")]
    assert run.pointer_cleared is False
    assert run.logged is None
    assert _pointer(layout).is_file()


def test_the_lane_log_records_the_archive_once(tmp_path):
    layout = _workspace(tmp_path)

    run = archive.run_archive(layout, today=TODAY, dry_run=False)

    assert run.logged is not None and _DONE in run.logged
    text = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert text.count(f"archived {_DONE}") == 1


def test_the_wide_lens_is_used_so_the_plan_is_not_half_done(tmp_path):
    # `okf_ext.moves` never reads `bundle.ignored`, so under `IGNORE` the
    # per-item artifacts are invisible and the plan reports `ok` while covering
    # only the item page. This asserts the artifact moves with its item.
    layout = _workspace(tmp_path)
    working = layout.bundle_dir / "work" / _DONE
    working.mkdir(parents=True, exist_ok=True)
    (working / "01-design-spec.md").write_text("# spec\n\nbody\n", encoding="utf-8")

    run = archive.run_archive(layout, today=TODAY, dry_run=False)

    assert run.result is not None and run.result.archived == (_DONE,)
    assert (layout.bundle_dir / "work" / "_archive" / _DONE / "01-design-spec.md").is_file()
    assert not working.exists()


def test_the_vertical_is_reachable_from_the_front_door():
    import graph_works_core

    assert graph_works_core.run_archive is archive.run_archive
    assert graph_works_core.ArchiveRun is archive.ArchiveRun


def test_wiki_slugs_defaults_to_no_wiki_involvement(tmp_path):
    layout = _workspace(tmp_path)
    _write_wiki_page(layout, "adrs/2026-08-01-example", "Example")

    run = archive.run_archive(layout, today=TODAY, dry_run=False)

    assert run.wiki_plan.tokens == ()
    assert run.wiki is not None and run.wiki.archived == ()
    assert _wiki_page(layout, "adrs/2026-08-01-example").is_file()


def test_a_targeted_wiki_archive_moves_the_page_unconditionally(tmp_path):
    layout = _workspace(tmp_path)
    _write_wiki_page(layout, "adrs/2026-08-01-example", "Example")

    run = archive.run_archive(layout, slugs=(), wiki_slugs=["adrs/2026-08-01-example"], today=TODAY, dry_run=False)

    assert run.wiki is not None and run.wiki.archived == ("adrs/2026-08-01-example",)
    assert not _wiki_page(layout, "adrs/2026-08-01-example").exists()
    assert _wiki_page(layout, "adrs/2026-08-01-example", archived=True).is_file()
    assert run.pointer_cleared is False
    assert run.result is not None and run.result.archived == ()


def test_a_joint_archive_logs_both_lanes_in_one_message(tmp_path):
    layout = _workspace(tmp_path)
    _write_wiki_page(layout, "adrs/2026-08-01-example", "Example")

    run = archive.run_archive(layout, wiki_slugs=["adrs/2026-08-01-example"], today=TODAY, dry_run=False)

    assert run.result is not None and run.result.archived == (_DONE,)
    assert run.wiki is not None and run.wiki.archived == ("adrs/2026-08-01-example",)
    assert run.logged == f"archived {_DONE}; archived wiki adrs/2026-08-01-example"


def test_a_cross_lane_touch_conflict_blocks_both_and_moves_neither(tmp_path):
    # The ADR references the work item, so the work-item lane's plan rewrites
    # the ADR's body (a referrer edit) -- but the wiki lane's own plan also
    # moves that same ADR. Applying both sequentially against one stale bundle
    # snapshot would have the wiki apply move the ADR using its pre-rewrite
    # content, discarding the work-item apply's already-written link fix.
    layout = _workspace(tmp_path)
    token = "adrs/2026-08-01-example"
    path = _wiki_page(layout, token)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: Example\ndescription: d\n---\n\n## Summary\nSee [the item](../work/{_DONE}.md).\n",
        encoding="utf-8",
    )

    run = archive.run_archive(layout, wiki_slugs=[token], today=TODAY, dry_run=False)

    assert run.ok is False
    assert run.conflict != ()
    assert run.result is None
    assert run.wiki is None
    assert _page(layout, _DONE).is_file()
    assert not _page(layout, _DONE, archived=True).exists()
    assert _wiki_page(layout, token).is_file()
    assert not _wiki_page(layout, token, archived=True).exists()


def test_a_refusal_on_either_lane_blocks_both(tmp_path):
    layout = _workspace(tmp_path)
    # A dest-exists conflict on the work-item side: `_DONE`'s archive
    # destination is already occupied by a stray file.
    conflict_dir = layout.bundle_dir / "work" / "_archive"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / f"{_DONE}.md").write_text("---\ntitle: stray\n---\n\nstray\n", encoding="utf-8")
    _write_wiki_page(layout, "adrs/2026-08-01-example", "Example")

    run = archive.run_archive(layout, wiki_slugs=["adrs/2026-08-01-example"], today=TODAY, dry_run=False)

    assert run.ok is False
    assert run.result is None
    assert run.wiki is None
    assert _page(layout, _DONE).is_file()
    assert _wiki_page(layout, "adrs/2026-08-01-example").is_file()
    assert not _wiki_page(layout, "adrs/2026-08-01-example", archived=True).exists()

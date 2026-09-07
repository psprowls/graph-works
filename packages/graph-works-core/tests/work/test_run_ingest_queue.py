"""`run_ingest_queue`: terminal items whose design spec has no matching
ingested `Source` page. Read-only, and derived — nothing is stored."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work

TODAY = date(2026, 8, 30)

_ITEM = """---
type: Bug
title: {slug}
description: d
status: stable
work_status: {work_status}
phase: done
effort: small
opened: 2026-08-01
updated: 2026-08-02
affects:
- packages/a
sources:
  - id: design
    resource: {resource}
    title: Design
---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""

_ITEM_NO_DESIGN = """---
type: Bug
title: {slug}
description: d
status: stable
work_status: resolved
phase: done
effort: small
opened: 2026-08-01
updated: 2026-08-02
affects:
- packages/a
---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""

_SOURCE_PAGE = """---
type: Source
title: Design for {slug}
description: d
source_kind: doc
source_path: sources/references/2026-08-{slug}.md
origin: {origin}
ingested: 2026-08-12
---

# Design for {slug}
"""


def _workspace(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write_item(layout, item_path: str, *, work_status: str = "resolved") -> str:
    slug = item_path.rsplit("/", 1)[-1]
    resource = f"/{item_path}/references/01-design.md"
    page = layout.bundle_dir / f"{item_path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(_ITEM.format(slug=slug, work_status=work_status, resource=resource), encoding="utf-8")
    artifact = layout.bundle_dir / item_path / "references" / "01-design.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# spec\n", encoding="utf-8")
    return resource


def _write_source(layout, slug: str, origin: str) -> None:
    page = layout.bundle_dir / "sources" / f"2026-08-{slug}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(_SOURCE_PAGE.format(slug=slug, origin=origin), encoding="utf-8")


def test_a_terminal_item_with_an_uningested_design_is_queued(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    resource = _write_item(layout, "work/bug-a")

    report = work.run_ingest_queue(layout)

    assert [entry.path for entry in report.pending] == ["work/bug-a"]
    assert report.pending[0].work_status == "resolved"
    assert report.pending[0].resource == resource
    assert report.pending[0].origin == "work/bug-a/references/01-design.md"


def test_a_matching_source_page_clears_the_item(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _write_item(layout, "work/bug-a")
    _write_source(layout, "bug-a", str(layout.bundle_dir / "work/bug-a/references/01-design.md"))

    assert work.run_ingest_queue(layout).pending == ()


def test_an_archived_lane_origin_still_clears_the_item(tmp_path: Path) -> None:
    """The proof survives the archive move in either direction."""
    layout = _workspace(tmp_path)
    _write_item(layout, "work/_archive/bug-a")
    _write_source(layout, "bug-a", str(layout.bundle_dir / "work/bug-a/references/01-design.md"))

    assert work.run_ingest_queue(layout).pending == ()


def test_a_source_page_without_an_origin_never_clears_anything(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _write_item(layout, "work/bug-a")
    page = layout.bundle_dir / "sources" / "2026-08-bug-a.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Source\ntitle: T\ndescription: d\nsource_kind: doc\n"
        "source_path: sources/references/2026-08-bug-a.md\ningested: 2026-08-12\n---\n\n# T\n",
        encoding="utf-8",
    )

    assert [entry.path for entry in work.run_ingest_queue(layout).pending] == ["work/bug-a"]


def test_an_item_with_no_design_source_is_never_queued(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    page = layout.bundle_dir / "work" / "bug-b.md"
    page.write_text(_ITEM_NO_DESIGN.format(slug="bug-b"), encoding="utf-8")

    assert work.run_ingest_queue(layout).pending == ()


def test_a_nonterminal_item_is_never_queued(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _write_item(layout, "work/bug-a", work_status="open")

    assert work.run_ingest_queue(layout).pending == ()


def test_wontfix_and_superseded_are_queued_like_resolved(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _write_item(layout, "work/bug-a", work_status="wontfix")
    _write_item(layout, "work/bug-c", work_status="superseded")

    assert [entry.path for entry in work.run_ingest_queue(layout).pending] == [
        "work/bug-a",
        "work/bug-c",
    ]


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_the_queue_writes_nothing_and_is_idempotent(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _write_item(layout, "work/bug-a")
    before = _snapshot(layout.bundle_dir)

    first = work.run_ingest_queue(layout)
    second = work.run_ingest_queue(layout)

    assert _snapshot(layout.bundle_dir) == before
    assert first == second

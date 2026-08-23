"""Canonical work archive composition with work-before-wiki ordering."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import graph_works_core
from graph_works_core import apply_init, plan_init
from graph_works_core.archive import commands as archive
from graph_works_core.workspace import provenance
from okf_ext.moves import Stranded

TODAY = date(2026, 8, 23)
DONE = "work/feature-done"
OPEN = "work/feature-open"


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Archive")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write(layout, path: str, *, work_status: str, phase: str) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: Feature\ntitle: {path}\ndescription: d\nstatus: stable\n"
        f"work_status: {work_status}\nphase: {phase}\neffort: medium\nopened: 2026-08-01\n"
        "updated: 2026-08-01\naffects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
    )


def _workspace(tmp_path: Path):
    layout = _layout(tmp_path)
    _write(layout, DONE, work_status="resolved", phase="done")
    _write(layout, OPEN, work_status="open", phase="execute")
    return layout


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_dry_run_plans_full_canonical_path_mapping(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    provenance.write_active_work(layout, DONE, "finish", updated=TODAY.isoformat())
    before_bundle = _snapshot(layout.bundle_dir)
    before_pointer = (layout.cache_dir / provenance.ACTIVE_WORK_FILENAME).read_bytes()
    result = archive.run_archive(layout, today=TODAY)
    assert result.plan.path_mapping == {DONE: "work/_archive/feature-done"}
    assert result.result is None
    assert result.logged is None
    assert _snapshot(layout.bundle_dir) == before_bundle
    assert (layout.cache_dir / provenance.ACTIVE_WORK_FILENAME).read_bytes() == before_pointer


def test_live_work_archive_goes_through_journal_and_clears_path_pointer(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    provenance.write_active_work(layout, DONE, "finish", updated=TODAY.isoformat())
    result = archive.run_archive(layout, today=TODAY, dry_run=False)
    assert result.result is not None and result.result.ok
    assert result.result.journal.is_file()
    assert (layout.bundle_dir / "work/_archive/feature-done.md").is_file()
    assert result.pointer_cleared is True
    assert result.logged == f"archived {DONE}"


def test_archive_wide_lens_moves_item_owned_reference_bytes(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    attachment = layout.bundle_dir / f"{DONE}/references/evidence.txt"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_bytes(b"opaque evidence\x00")
    preview = archive.run_archive(layout, paths=(DONE,), today=TODAY)
    assert any(move.source == f"{DONE}/references/evidence.txt" for move in preview.plan.moves)
    result = archive.run_archive(layout, paths=(DONE,), today=TODAY, dry_run=False)
    assert result.result is not None and result.result.ok
    assert (
        layout.bundle_dir / "work/_archive/feature-done/references/evidence.txt"
    ).read_bytes() == b"opaque evidence\x00"


def test_refused_work_preflight_blocks_wiki_apply(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    wiki = layout.bundle_dir / "adrs/example.md"
    wiki.parent.mkdir(parents=True)
    wiki.write_text("---\ntitle: Example\ndescription: d\nstatus: stable\n---\n", encoding="utf-8")
    result = archive.run_archive(
        layout,
        paths=(OPEN,),
        wiki_slugs=("adrs/example",),
        today=TODAY,
        dry_run=False,
    )
    assert not result.plan.ok
    assert result.result is None and result.wiki is None
    assert wiki.is_file()


def test_wiki_archive_starts_only_after_successful_work_transaction(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    wiki = layout.bundle_dir / "adrs/example.md"
    wiki.parent.mkdir(parents=True)
    wiki.write_text("---\ntitle: Example\ndescription: d\nstatus: stable\n---\n", encoding="utf-8")
    result = archive.run_archive(
        layout,
        paths=(DONE,),
        wiki_slugs=("adrs/example",),
        today=TODAY,
        dry_run=False,
    )
    assert result.result is not None and result.result.ok
    assert result.wiki is not None and result.wiki.archived == ("adrs/example",)
    assert (layout.bundle_dir / "adrs/_archive/example.md").is_file()


def test_cross_lane_touched_member_conflict_blocks_both_plans(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    wiki = layout.bundle_dir / "adrs/example.md"
    wiki.parent.mkdir(parents=True)
    wiki.write_text(
        "---\ntitle: Example\ndescription: d\nstatus: stable\n---\n\nSee [the item](../work/feature-done.md).\n",
        encoding="utf-8",
    )
    result = archive.run_archive(
        layout,
        paths=(DONE,),
        wiki_slugs=("adrs/example",),
        today=TODAY,
        dry_run=False,
    )
    assert result.conflict == ("adrs/example.md",)
    assert result.result is None and result.wiki is None
    assert (layout.bundle_dir / f"{DONE}.md").is_file()
    assert wiki.is_file()


def test_archive_remains_hoisted_at_package_root() -> None:
    assert graph_works_core.run_archive is archive.run_archive


def test_archive_stranded_warnings_are_projected_by_core() -> None:
    work = Stranded(member="work/citing.md", target="work/a.md", line=3)
    wiki = Stranded(member="tutorials/citing.md", target="tutorials/b.md", line=5)
    run = SimpleNamespace(
        plan=SimpleNamespace(move_plan=SimpleNamespace(stranded=(work,))),
        wiki_plan=SimpleNamespace(moves=SimpleNamespace(stranded=(wiki,))),
    )

    warnings = archive.stranded_warnings(run)
    assert len(warnings) == 2
    assert warnings[0].startswith("work items: ! 1 inbound [[wikilink]] reference(s)")
    assert warnings[1].startswith("wiki pages: ! 1 inbound [[wikilink]] reference(s)")

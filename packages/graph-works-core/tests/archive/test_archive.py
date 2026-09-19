"""Canonical work archive composition with work-before-wiki ordering."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import graph_works_core
import pytest
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


def _workspace_with_referring_log(tmp_path: Path):
    """Like `_workspace`, but `log.md` already carries an OKF markdown link
    into `DONE` -- the trigger for `work/bug-archive-duplicate-log-write`:
    reference repair plans its own `log.md` write, which the archive-entry
    append must chain onto rather than duplicate."""
    layout = _workspace(tmp_path)
    log_path = layout.bundle_dir / "log.md"
    log_path.write_text(
        log_path.read_text(encoding="utf-8") + f"- filed [{DONE}]({DONE}.md)\n",
        encoding="utf-8",
    )
    return layout


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _wiki_workspace(tmp_path: Path, *, log_links_page: bool):
    layout = _layout(tmp_path)
    sources = layout.bundle_dir / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "one.md").write_text(
        "---\ntype: Source\ntitle: One\ndescription: d\n---\n\nbody\n", encoding="utf-8", newline=""
    )
    if log_links_page:
        log = layout.bundle_dir / "log.md"
        log.write_text(
            log.read_text(encoding="utf-8") + "\n## [2026-08-22] ingest\n- ingested [one](sources/one.md)\n",
            encoding="utf-8",
            newline="",
        )
    return layout


@pytest.mark.parametrize("log_links_page", (False, True))
def test_wiki_only_archive_keeps_its_log_entry(tmp_path: Path, log_links_page: bool) -> None:
    """The wiki half's referrer rewrite of log.md must not erase the archive entry."""
    layout = _wiki_workspace(tmp_path, log_links_page=log_links_page)

    run = archive.run_archive(layout, (), ["sources/one"], today=TODAY, dry_run=False)

    assert run.ok and run.wiki is not None and run.wiki.ok
    assert run.logged == "archived wiki sources/one"
    log = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert log.count("- archived wiki sources/one") == 1
    if log_links_page:
        assert "[one](sources/_archive/one.md)" in log


def test_wiki_only_dry_run_reports_the_entry_and_writes_nothing(tmp_path: Path) -> None:
    layout = _wiki_workspace(tmp_path, log_links_page=True)
    before = _snapshot(layout.bundle_dir)

    run = archive.run_archive(layout, (), ["sources/one"], today=TODAY, dry_run=True)

    assert run.logged == "archived wiki sources/one"
    assert _snapshot(layout.bundle_dir) == before


def test_wiki_only_archive_leaves_an_invalid_pointer_alone(tmp_path: Path) -> None:
    """An empty work-root set must not trigger clear_active_work's invalid-pointer delete."""
    layout = _wiki_workspace(tmp_path, log_links_page=False)
    pointer = layout.cache_dir / provenance.ACTIVE_WORK_FILENAME
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"path": "not-a-path-native-id"}), encoding="utf-8", newline="")

    run = archive.run_archive(layout, (), ["sources/one"], today=TODAY, dry_run=False)

    assert run.pointer_cleared is False
    assert pointer.exists()


def test_work_archive_still_clears_an_invalid_pointer(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    pointer = layout.cache_dir / provenance.ACTIVE_WORK_FILENAME
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"path": "not-a-path-native-id"}), encoding="utf-8", newline="")

    run = archive.run_archive(layout, [DONE], today=TODAY, dry_run=False)

    assert run.pointer_cleared is True
    assert not pointer.exists()


def test_dry_run_plans_full_canonical_path_mapping(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    provenance.write_active_work(layout, DONE, "finish", updated=TODAY.isoformat())
    before_bundle = _snapshot(layout.bundle_dir)
    before_pointer = (layout.cache_dir / provenance.ACTIVE_WORK_FILENAME).read_bytes()
    result = archive.run_archive(layout, today=TODAY)
    assert result.plan.path_mapping == {DONE: "work/_archive/feature-done"}
    assert result.result is None
    assert result.logged == f"archived {DONE}", "preview reports the same message apply would log"
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


def test_archive_composes_log_entry_write_with_reference_repair(tmp_path: Path) -> None:
    """`work/bug-archive-duplicate-log-write`: when `log.md` links into the
    archived item, reference repair already plans a `log.md` write; the
    archive-entry append must chain onto it instead of layering a second,
    independently-sourced write for the same member -- which trips the
    transaction layer's duplicate-target guard and refuses the whole apply."""
    layout = _workspace_with_referring_log(tmp_path)
    result = archive.run_archive(layout, today=TODAY, dry_run=False)
    assert result.result is not None and result.result.ok, result.result.failures if result.result is not None else None
    log_text = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert f"[{DONE}](work/_archive/feature-done.md)" in log_text, "reference repair survives"
    assert f"archived {DONE}" in log_text, "archive-entry append survives"


def test_dry_run_log_write_matches_the_write_actually_applied(tmp_path: Path) -> None:
    """Preview and apply must plan `log.md` identically -- the secondary
    defect from `work/bug-archive-duplicate-log-write`: `dry_run=True`
    returned before the archive-entry append, so a preview that reported
    `ok: true` could still refuse on apply."""
    layout = _workspace_with_referring_log(tmp_path)
    preview = archive.run_archive(layout, today=TODAY)
    previewed = next(write for write in preview.plan.writes if write.member == "log.md")
    result = archive.run_archive(layout, today=TODAY, dry_run=False)
    assert result.result is not None and result.result.ok, result.result.failures if result.result is not None else None
    assert (layout.bundle_dir / "log.md").read_bytes() == previewed.after


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


def _split_layout(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / ".git").mkdir(parents=True)
    code = tmp_path / "code"
    (code / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(vault / ".works", today=TODAY, topic="Split")).layout
    layout.manifest_path.write_text(
        f'version: 1\nrepositories:\n  "code":\n    path: {json.dumps(str(code))}\n',
        encoding="utf-8",
    )
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def test_split_topology_archive_validates_against_the_declared_code_repo(tmp_path: Path) -> None:
    layout = _split_layout(tmp_path)
    _write(layout, DONE, work_status="resolved", phase="done")
    result = archive.run_archive(layout, today=TODAY, dry_run=False)
    assert result.result is not None
    assert result.result.ok, result.result.failures


def _write_referring(layout, path: str, *, work_status: str, phase: str, type_: str, refers_to: str) -> None:
    """Like `_write`, but for an item that stays active and links to *refers_to*.

    Archiving the linked target rewrites this item's body (a referrer edit),
    which keeps it in the mutation's postcondition `validate_paths` even
    though it was never itself archived -- the only way `targets.affects-missing`
    gets a chance to fire against a still-active item in the same archive run.
    """
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {type_}\ntitle: {path}\ndescription: d\nstatus: stable\n"
        f"work_status: {work_status}\nphase: {phase}\neffort: medium\nopened: 2026-08-01\n"
        "updated: 2026-08-01\naffects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n"
        f"| Action | Done when | Rationale |\n| --- | --- | --- |\n\nSee [it]({refers_to}).\n",
        encoding="utf-8",
    )


def test_split_topology_archive_validates_referring_item_against_the_declared_code_repo(tmp_path: Path) -> None:
    """A single archived item is exempt from `targets.affects-missing` (an
    archived item is never `active()`), so the sibling test above cannot
    distinguish a correct `repo_root` from a wrong one. A still-active item
    that references the archived item gets a referrer-rewrite edit as part of
    the same archive plan, which keeps it in postcondition `validate_paths`
    -- this is the scenario that actually depends on `repo_root` resolving to
    the declared code repo rather than `layout.repo_root`'s `.git` walk-up
    (the vault, in a split topology)."""
    layout = _split_layout(tmp_path)
    _write(layout, DONE, work_status="resolved", phase="done")
    _write_referring(layout, OPEN, work_status="open", phase="execute", type_="Bug", refers_to="feature-done.md")
    result = archive.run_archive(layout, paths=(DONE,), today=TODAY, dry_run=False)
    assert result.result is not None
    assert result.result.ok, result.result.failures


def test_two_declared_repos_archive_validates_against_every_declared_repo(tmp_path: Path) -> None:
    """Two declared repositories no longer refuse: the referring item's
    `affects` resolves under the second repo, and the postcondition gate
    checks it against every declared root."""
    layout = _split_layout(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    code = tmp_path / "code"
    layout.manifest_path.write_text(
        "version: 1\nrepositories:\n"
        f'  "other":\n    path: {json.dumps(str(other))}\n'
        f'  "code":\n    path: {json.dumps(str(code))}\n',
        encoding="utf-8",
    )
    _write(layout, DONE, work_status="resolved", phase="done")
    _write_referring(layout, OPEN, work_status="open", phase="execute", type_="Bug", refers_to="feature-done.md")
    result = archive.run_archive(layout, paths=(DONE,), today=TODAY, dry_run=False)
    assert result.result is not None
    assert result.result.ok, result.result.failures

"""Index display reads must match the full-load oracle without live rereads."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from conftest import FIXTURE_BUNDLES
from graph_works_core.read_session import BundleSession, IndexSession
from graph_works_core.workspace.bundle import CLONE_IGNORE, CLONE_PRUNE
from okf_ext.readindex import open_index, read, reconcile
from work_tracker_okf.items import IGNORE

OVERLAYS = [pytest.param((), id="no-overlay"), pytest.param(IGNORE, id="work-ignore")]


def make_session(index, view, root, db):
    return IndexSession(index, view, root=root, db_path=db)


def compare(root: Path, db: Path, ignore) -> None:
    oracle = BundleSession(root, fallback=None)
    with open_index(db, root, ignore=CLONE_IGNORE, prune=CLONE_PRUNE) as index:
        reconcile(index)
        with read(index) as view:
            session = make_session(index, view, root, db)
            assert session.members(ignore=ignore) == oracle.members(ignore=ignore)
            for kind in ("concept", "index", "log", "asset", "ignored"):
                assert session.members(kind=kind, ignore=ignore) == oracle.members(kind=kind, ignore=ignore)
            for prefix in ("work/", "docs/", "absent/"):
                for type_ in (None, "Epic", "Bug", "Explanation", "Absent"):
                    assert session.members(prefix=prefix, type=type_, ignore=ignore) == oracle.members(
                        prefix=prefix, type=type_, ignore=ignore
                    )
            for row in oracle.members(ignore=ignore):
                assert session.member(row.id, ignore=ignore) == oracle.member(row.id, ignore=ignore)
                assert session.member(f" {row.id} ", ignore=ignore) == oracle.member(row.id, ignore=ignore)
                assert session.headings(row.id) == oracle.headings(row.id)
                if row.concept_id is not None:
                    cid = row.concept_id
                    assert session.outlinks(cid, ignore=ignore) == oracle.outlinks(cid, ignore=ignore)
                    assert session.backlinks(cid, ignore=ignore) == oracle.backlinks(cid, ignore=ignore)
                    assert session.broken(cid, ignore=ignore) == oracle.broken(cid, ignore=ignore)
            assert session.broken(ignore=ignore) == oracle.broken(ignore=ignore)
            assert session.diagnostics(ignore=ignore) == oracle.diagnostics(ignore=ignore)


@pytest.mark.parametrize("ignore", OVERLAYS)
@pytest.mark.parametrize("root", FIXTURE_BUNDLES, ids=lambda p: p.name)
def test_fixture_bundles(root: Path, ignore, tmp_path: Path) -> None:
    compare(root, tmp_path / "i.db", ignore)


@pytest.mark.parametrize("ignore", OVERLAYS)
def test_generated_workspace_bundle(workspace, ignore, tmp_path: Path) -> None:
    compare(workspace.bundle_dir, tmp_path / "i.db", ignore)


@pytest.mark.skipif(sys.platform == "win32" or getattr(os, "geteuid", lambda: 1)() == 0, reason="chmod")
@pytest.mark.parametrize("ignore", OVERLAYS)
def test_unreadable_directory_survives_overlay(workspace, ignore, tmp_path: Path) -> None:
    locked = workspace.bundle_dir / "work/epic-a/references/locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        compare(workspace.bundle_dir, tmp_path / "i.db", ignore)
    finally:
        locked.chmod(0o755)


def test_work_snapshot_equals_full_load(index_session, workspace) -> None:
    for ignore in (IGNORE, (), ("work/*",)):
        assert tuple(index_session.work_snapshot(ignore=ignore)) == tuple(
            BundleSession(workspace.bundle_dir, fallback=None).work_snapshot(ignore=ignore)
        )


def test_pinned_rows_survive_file_edit(index_session, workspace) -> None:
    generation = index_session.generation
    (workspace.bundle_dir / "work/epic-a.md").write_text("---\ntitle: Changed\n---\n", encoding="utf-8", newline="\n")
    assert index_session.work_snapshot().by_path["work/epic-a"].title == "A"
    assert index_session.member("work/epic-a.md").title == "A"
    assert (index_session.backend, index_session.fallback, index_session.generation) == ("index", None, generation)


def test_body_citation_is_an_accepted_projection_difference(workspace, tmp_path: Path) -> None:
    root = workspace.bundle_dir
    (root / "work/legacy.md").write_text(
        "---\ntype: Bug\ntitle: L\nwork_status: open\n---\n# Citations\n\n- [s](/docs/explanations/p.md)\n",
        encoding="utf-8",
        newline="\n",
    )
    db = tmp_path / "i.db"
    with open_index(db, root, ignore=CLONE_IGNORE, prune=CLONE_PRUNE) as index:
        reconcile(index)
        with read(index) as view:
            rows = make_session(index, view, root, db).work_snapshot()
    full = BundleSession(root, fallback=None).work_snapshot()
    assert full.by_path["work/legacy"].sources and rows.by_path["work/legacy"].sources == ()
    assert rows.by_path["work/legacy"].has_design_artifact == full.by_path["work/legacy"].has_design_artifact


def test_concurrent_reconcile_does_not_change_pinned_results(workspace, tmp_path: Path) -> None:
    root = workspace.bundle_dir
    db = tmp_path / "i.db"
    oracle = BundleSession(root, fallback=None)
    # Pin both full-load policies before updating disk and the persisted index.
    oracle.members()
    oracle.members(ignore=IGNORE)
    with open_index(db, root, ignore=CLONE_IGNORE, prune=CLONE_PRUNE) as index:
        reconcile(index)
        with read(index) as view:
            session = make_session(index, view, root, db)
            generation = session.generation
            (root / "work/epic-a.md").write_text(
                "---\ntype: Epic\ntitle: Changed\n---\n# New\n", encoding="utf-8", newline="\n"
            )
            (root / "work/epic-a/references/undecodable.md").write_text("Now readable", encoding="utf-8", newline="\n")
            (root / "late.md").write_text("Late", encoding="utf-8", newline="\n")
            with open_index(db, root, ignore=CLONE_IGNORE, prune=CLONE_PRUNE) as writer:
                reconcile(writer)
                with read(writer) as fresh:
                    assert fresh.generation > generation
                    assert fresh.member("work/epic-a.md").title == "Changed"
            for ignore in ((), IGNORE):
                assert session.members(ignore=ignore) == oracle.members(ignore=ignore)
                assert session.diagnostics(ignore=ignore) == oracle.diagnostics(ignore=ignore)
                assert session.broken(ignore=ignore) == oracle.broken(ignore=ignore)
                assert session.outlinks("work/epic-a", ignore=ignore) == oracle.outlinks("work/epic-a", ignore=ignore)
                assert session.backlinks("work/epic-a", ignore=ignore) == oracle.backlinks("work/epic-a", ignore=ignore)
                assert tuple(session.work_snapshot(ignore=ignore)) == tuple(oracle.work_snapshot(ignore=ignore))
            assert session.member("late.md", ignore=IGNORE) is None
            assert session.headings("work/epic-a.md") == oracle.headings("work/epic-a.md")
            assert session.generation == generation


def test_native_dates_and_nested_keys_keep_accepted_row_projection(workspace, tmp_path: Path) -> None:
    from datetime import date

    root = workspace.bundle_dir
    (root / "work/native.md").write_text(
        "---\ntype: Bug\ntitle: Native\ntags: [2026-10-02]\nsources:\n"
        "  - id: s\n    resource: /docs/explanations/p.md\n    when: 2026-10-02\n    nested: [{1: one}]\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    db = tmp_path / "i.db"
    with open_index(db, root, ignore=CLONE_IGNORE, prune=CLONE_PRUNE) as index:
        reconcile(index)
        with read(index) as view:
            session = make_session(index, view, root, db)
            row = session.work_snapshot().by_path["work/native"]
            assert session.backend == "index"
            assert row.tags == ("2026-10-02",)
            assert row.sources[0].extra["when"] == "2026-10-02"
            assert row.sources[0].extra["nested"] == [{"1": "one"}]
    full = BundleSession(root, fallback=None).work_snapshot().by_path["work/native"]
    assert full.tags == ()
    assert full.sources[0].extra["when"] == date(2026, 10, 2)
    assert full.sources[0].extra["nested"] == [{1: "one"}]

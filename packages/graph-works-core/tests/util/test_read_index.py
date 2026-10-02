"""Read-index diagnostics preserve disk state; explicit maintenance reconciles it."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest
from graph_works_core.read_session import location
from graph_works_core.util.read_index import TABLES, ReadIndexBusy, ReadIndexUnavailable, run_read_index
from graph_works_core.workspace.init import apply_init, plan_init
from okf_ext import readindex


@pytest.fixture
def layout(tmp_path):
    layout = apply_init(plan_init(tmp_path / ".works", today=date(2026, 10, 2), topic="t")).layout
    path = layout.bundle_dir / "a.md"
    path.write_text("---\ntitle: A\n---\n", encoding="utf-8", newline="\n")
    os.utime(path, ns=(1_600_000_000_000_000_000,) * 2)
    return layout


def disk_state(layout):
    return {p.relative_to(layout.root): p.read_bytes() for p in layout.root.rglob("*") if p.is_file()}


def test_missing_inspect_creates_nothing(layout):
    before = disk_state(layout)
    report = run_read_index(layout)
    assert (report.mode, report.exists, report.generation, report.error) == ("inspect", False, None, None)
    assert report.tables == report.kinds == {}
    assert report.stored_fingerprint is report.schema_version is report.projection_version is None
    assert report.okf_io_version is report.last_reconcile_ns is None
    assert report.parsed is report.drift is report.elapsed_s is report.rebuilt_reason is None
    assert report.unreadable_files == 0
    assert not location.database_path(layout).parent.exists()
    assert disk_state(layout) == before


def test_rebuild_then_closed_wal_inspect_has_counts_and_no_sidecars(layout):
    ticks = iter([1.0, 3.5])
    rebuilt = run_read_index(layout, rebuild=True, clock=lambda: next(ticks))
    assert (rebuilt.mode, rebuilt.rebuilt_reason, rebuilt.elapsed_s) == ("rebuild", "created", 2.5)
    assert rebuilt.parsed is not None and rebuilt.parsed >= 1
    db = location.database_path(layout)
    assert db.read_bytes()[18:20] == b"\x02\x02"
    assert list(db.parent.iterdir()) == [db]
    before = disk_state(layout)
    report = run_read_index(layout)
    assert report.exists and report.error is None and report.generation is not None
    assert set(report.tables) == set(TABLES)
    assert report.kinds["concept"] >= 1
    assert report.stored_fingerprint == report.current_fingerprint == location.fingerprint(layout)
    assert report.last_reconcile_ns > 0
    assert disk_state(layout) == before
    with pytest.raises(FrozenInstanceError):
        report.exists = False
    with pytest.raises(TypeError):
        report.tables["members"] = -1


def test_live_wal_inspect_refuses_without_files_or_stale_counts(layout):
    run_read_index(layout, rebuild=True)
    db = location.database_path(layout)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE meta SET value='999' WHERE key='generation'")
        conn.commit()
        assert Path(f"{db}-wal").exists()
        before = disk_state(layout)
        report = run_read_index(layout)
        assert report.exists and "wal" in report.error.lower()
        assert report.generation is None and report.tables == report.kinds == {}
        assert disk_state(layout) == before


def test_changing_main_file_is_refused(layout, monkeypatch):
    run_read_index(layout, rebuild=True)
    db = location.database_path(layout)
    original = Path.read_bytes

    def changing_read(path):
        data = original(path)
        if path == db:
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        return data

    monkeypatch.setattr(Path, "read_bytes", changing_read)
    report = run_read_index(layout)
    assert report.exists and "changed" in report.error.lower()
    assert report.generation is None and report.tables == {}
    assert list(db.parent.iterdir()) == [db]


@pytest.mark.parametrize(
    "key,value",
    [
        ("generation", "garbage"),
        ("last_reconcile_ns", "1.5"),
        ("generation", None),
        ("last_reconcile_ns", None),
        ("schema_version", None),
        ("projection_version", None),
        ("okf_io_version", None),
        ("schema_version", "999"),
        ("projection_version", "999"),
        ("okf_io_version", "unsupported"),
    ],
)
def test_invalid_metadata_is_reported_without_changing_bytes(layout, key, value):
    run_read_index(layout, rebuild=True)
    with sqlite3.connect(location.database_path(layout)) as conn:
        if value is None:
            conn.execute("DELETE FROM meta WHERE key=?", (key,))
        else:
            conn.execute("UPDATE meta SET value=? WHERE key=?", (value, key))
    conn.close()
    before = disk_state(layout)
    report = run_read_index(layout)
    assert report.exists and key in report.error
    assert report.generation is None and report.tables == {}
    assert disk_state(layout) == before


def test_corrupt_bytes_are_reported_without_changes(layout):
    db = location.database_path(layout)
    db.parent.mkdir(parents=True)
    db.write_bytes(b"not a database")
    before = disk_state(layout)
    report = run_read_index(layout)
    assert report.exists and report.error
    assert disk_state(layout) == before


def test_verify_detects_stored_hash_discrepancy(layout):
    run_read_index(layout, rebuild=True)
    with sqlite3.connect(location.database_path(layout)) as conn:
        conn.execute("UPDATE members SET sha256=? WHERE id='a.md'", ("0" * 64,))
    conn.close()
    report = run_read_index(layout, verify=True)
    assert report.mode == "verify" and report.drift == ("a.md",)
    assert report.parsed == 0 and report.elapsed_s is report.error is None


def test_verify_reconciles_ctime_rewrite_and_new_members(layout):
    run_read_index(layout, rebuild=True)
    path = layout.bundle_dir / "a.md"
    stat = path.stat()
    path.write_text("---\ntitle: B\n---\n", encoding="utf-8", newline="\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    (layout.bundle_dir / "new.md").write_text("---\ntitle: New\n---\n", encoding="utf-8", newline="\n")
    report = run_read_index(layout, verify=True)
    assert report.parsed >= 2 and report.drift == ()
    with sqlite3.connect(report.db_path) as conn:
        assert conn.execute("SELECT title FROM members WHERE id='a.md'").fetchone() == ("B",)


def test_rebuild_discards_previous_cache_and_sidecars(layout):
    run_read_index(layout, rebuild=True)
    db = location.database_path(layout)
    db.write_bytes(b"corrupt")
    for path in location.sidecar_paths(db)[1:]:
        path.write_bytes(b"leftover")
    report = run_read_index(layout, rebuild=True)
    assert report.error is None and report.rebuilt_reason == "created"
    assert report.parsed >= 1
    assert list(db.parent.iterdir()) == [db]


@pytest.mark.parametrize("mode", ["inspect", "verify", "rebuild"])
def test_disabled_does_not_skip_explicit_diagnostics(layout, mode):
    layout.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")
    report = run_read_index(layout, verify=mode == "verify", rebuild=mode == "rebuild")
    assert (report.enabled, report.backend, report.backend_reason) == (False, "bundle", "disabled")
    assert report.exists == (mode != "inspect") and report.error is None


def test_mutually_exclusive_modes_fail_before_io(layout):
    before = disk_state(layout)
    with pytest.raises(ValueError, match="--verify and --rebuild cannot be combined"):
        run_read_index(layout, verify=True, rebuild=True)
    assert disk_state(layout) == before


@pytest.mark.parametrize("rebuild", [False, True])
@pytest.mark.parametrize(
    "operation,producer,core",
    [
        ("reconcile", readindex.IndexBusy, ReadIndexBusy),
        ("open_index", readindex.IndexUnavailable, ReadIndexUnavailable),
    ],
)
def test_producer_failures_become_core_oserrors(layout, monkeypatch, rebuild, operation, producer, core):
    error = producer("injected failure")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(readindex, operation, fail)
    with pytest.raises(core) as caught:
        run_read_index(layout, verify=not rebuild, rebuild=rebuild)
    assert isinstance(caught.value, OSError) and caught.value.__cause__ is error

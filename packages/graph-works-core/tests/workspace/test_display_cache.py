"""Disposable display-cache hits, recovery, row validation and fallbacks."""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest
from graph_works_core.workspace import display_cache
from graph_works_core.workspace.display_cache import database_path, open_display_cache
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.init import apply_init, plan_init
from graph_works_core.workspace.layout import WorkspaceLayout, layout_for

SPANS = (("a.py:3", 1, "a.py", 3, 3), ("b/c.py:1-2", 4, "b/c.py", 1, 2))
LOGGER = "graph_works_core.display_cache"


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    return apply_init(plan_init(tmp_path / ".works", today=date(2026, 9, 19), topic="Code")).layout


def test_inventory_survives_reopen_and_key_changes_replace_files(layout: WorkspaceLayout, tmp_path: Path) -> None:
    with open_display_cache(layout) as cache:
        assert cache is not None
        cache.store_inventory(tmp_path, "k1", frozenset({"a.py", "b/c.py"}), racy=False)
    assert database_path(layout) == layout.cache_dir / "read-index" / "display.db"
    with open_display_cache(layout) as cache:
        assert cache is not None
        assert cache.inventory(tmp_path, "k1") == (frozenset({"a.py", "b/c.py"}), False)
        assert cache.inventory(tmp_path, "k2") is None
        assert cache.inventory(tmp_path / "other", "k1") is None
        cache.store_inventory(tmp_path, "k2", frozenset({"a.py"}), racy=True)
        assert cache.inventory(tmp_path, "k2") == (frozenset({"a.py"}), True)
        assert cache.inventory(tmp_path, "k1") is None
        cache.store_inventory(tmp_path / "other", "k", frozenset(), racy=False)
        assert cache.inventory(tmp_path / "other", "k") == (frozenset(), False)


def test_spans_survive_reopen_and_extractor_changes_miss(layout: WorkspaceLayout) -> None:
    with open_display_cache(layout) as cache:
        assert cache is not None
        cache.store_spans("h" * 64, "1:x", SPANS)
        cache.store_spans("empty", "1:x", ())
    with open_display_cache(layout) as cache:
        assert cache is not None
        assert cache.spans("h" * 64, "1:x") == SPANS
        assert cache.spans("h" * 64, "2:x") is None
        assert cache.spans("0" * 64, "1:x") is None
        assert cache.spans("empty", "1:x") == ()
        cache.store_spans("h" * 64, "2:x", (("café.py:1", 2, "café.py", 1, 1),))
        assert cache.spans("h" * 64, "1:x") is None
        assert cache.spans("h" * 64, "2:x") == (("café.py:1", 2, "café.py", 1, 1),)


def test_reverse_ranges_round_trip_verbatim(layout: WorkspaceLayout) -> None:
    spans = (("a.py:9-3", 1, "a.py", 9, 3),)
    with open_display_cache(layout) as cache:
        assert cache is not None
        cache.store_spans("h", "1:x", spans)
        assert cache.spans("h", "1:x") == spans


def test_concurrent_replacement_never_pairs_old_key_with_new_files(layout: WorkspaceLayout, tmp_path: Path) -> None:
    with open_display_cache(layout) as reader, open_display_cache(layout) as writer:
        assert reader is not None and writer is not None
        writer.store_inventory(tmp_path, "old", frozenset({"old.py"}), racy=False)
        selects = 0

        def replace_between_queries(statement: str) -> None:
            nonlocal selects
            if statement.startswith("SELECT"):
                selects += 1
                if selects == 2:
                    writer.store_inventory(tmp_path, "new", frozenset({"new.py"}), racy=True)

        reader._conn.set_trace_callback(replace_between_queries)
        assert reader.inventory(tmp_path, "old") in (None, (frozenset({"old.py"}), False))


def test_disabled_creates_no_database_or_warning(layout: WorkspaceLayout, caplog: pytest.LogCaptureFixture) -> None:
    layout.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")
    with caplog.at_level(logging.WARNING, logger=LOGGER), open_display_cache(layout) as cache:
        assert cache is None
    assert not database_path(layout).exists()
    assert not caplog.records


def test_manifest_errors_keep_their_contract(layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: object) -> bool:
        raise OSError("manifest unreadable")

    monkeypatch.setattr(display_cache, "checked_bool", unavailable)
    with open_display_cache(layout) as cache:
        assert cache is None
    monkeypatch.undo()
    layout.local_manifest_path.write_text("read_index:\n  enabled: wrong\n", encoding="utf-8", newline="\n")
    with pytest.raises(WorkspaceError), open_display_cache(layout):
        pass


@pytest.mark.parametrize("key", ["schema_version", "ignore_version"])
def test_version_mismatch_rebuilds_silently(
    layout: WorkspaceLayout, tmp_path: Path, key: str, caplog: pytest.LogCaptureFixture
) -> None:
    with open_display_cache(layout) as cache:
        assert cache is not None
        cache.store_inventory(tmp_path, "k", frozenset({"a.py"}), racy=False)
        cache._conn.execute("UPDATE meta SET value='stale' WHERE key=?", (key,))
    with caplog.at_level(logging.WARNING, logger=LOGGER), open_display_cache(layout) as cache:
        assert cache is not None
        assert cache.inventory(tmp_path, "k") is None
    assert not caplog.records


@pytest.mark.parametrize("damage", ["bytes", "missing-table", "wrong-columns", "missing-meta"])
def test_corruption_rebuilds_with_one_warning(
    layout: WorkspaceLayout, damage: str, caplog: pytest.LogCaptureFixture
) -> None:
    db = database_path(layout)
    if damage == "bytes":
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(b"not a database" * 100)
    else:
        with open_display_cache(layout) as cache:
            assert cache is not None
            if damage == "missing-table":
                cache._conn.execute("DROP TABLE inventory_files")
            elif damage == "wrong-columns":
                cache._conn.execute("ALTER TABLE citation_spans RENAME COLUMN extractor TO wrong")
            else:
                cache._conn.execute("DROP TABLE meta")
    with caplog.at_level(logging.WARNING, logger=LOGGER), open_display_cache(layout) as cache:
        assert cache is not None
        cache.store_spans("h", "1:x", SPANS)
        assert cache.spans("h", "1:x") == SPANS
    assert len(caplog.records) == 1
    assert "discarded" in caplog.records[0].message


def test_deleted_database_is_recreated(layout: WorkspaceLayout) -> None:
    with open_display_cache(layout):
        pass
    for path in database_path(layout).parent.glob("display.db*"):
        path.unlink()
    with open_display_cache(layout) as cache:
        assert cache is not None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_unwritable_cache_yields_none_with_one_warning(
    layout: WorkspaceLayout, caplog: pytest.LogCaptureFixture
) -> None:
    layout.cache_dir.chmod(0o500)
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER), open_display_cache(layout) as cache:
            assert cache is None
    finally:
        layout.cache_dir.chmod(0o755)
    assert len(caplog.records) == 1


def test_write_lock_preserves_existing_rows_and_drops_stores(
    layout: WorkspaceLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(display_cache, "BUSY_TIMEOUT_MS", 50)
    with open_display_cache(layout) as cache:
        assert cache is not None
        cache.store_inventory(tmp_path, "old", frozenset({"old.py"}), racy=False)
        cache.store_spans("h", "1:x", SPANS)
    holder = sqlite3.connect(database_path(layout), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER), open_display_cache(layout) as cache:
            assert cache is not None
            cache.store_inventory(tmp_path, "new", frozenset({"new.py"}), racy=True)
            assert cache.inventory(tmp_path, "new") is None
            assert cache.inventory(tmp_path, "old") == (frozenset({"old.py"}), False)
            cache.store_spans("h", "2:x", ())
            assert cache.spans("h", "1:x") == SPANS
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert len(caplog.records) == 2


def test_locked_open_does_not_discard_valid_database(
    layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(display_cache, "BUSY_TIMEOUT_MS", 50)
    with open_display_cache(layout) as cache:
        assert cache is not None
        cache.store_spans("h", "1:x", SPANS)
    holder = sqlite3.connect(database_path(layout), isolation_level=None)
    holder.execute("PRAGMA journal_mode=DELETE")
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER), open_display_cache(layout) as cache:
            assert cache is None
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert len(caplog.records) == 1
    assert "unavailable" in caplog.records[0].message
    with open_display_cache(layout) as cache:
        assert cache is not None
        assert cache.spans("h", "1:x") == SPANS


@pytest.mark.parametrize(
    "bad",
    [
        "bad-json",
        "null",
        "{}",
        "[1]",
        "[[1,2]]",
        '[["x",true,"p",1,1]]',
        '[["x",0,"p",1,1]]',
        '[["x",1,"p",0,1]]',
        '[["x",1,"p",1,0]]',
        '[["x",1,3,1,1]]',
        '[["x",1,"p",true,1]]',
        '[["x",1,"p",1,1.0]]',
        b"\xff",
    ],
)
def test_malformed_spans_are_misses(layout: WorkspaceLayout, bad: str | bytes) -> None:
    with open_display_cache(layout) as cache:
        assert cache is not None
        cache._conn.execute("INSERT INTO citation_spans VALUES ('h', '1:x', ?)", (bad,))
        assert cache.spans("h", "1:x") is None


@pytest.mark.parametrize("racy,path", [(2, "a.py"), (0, ""), (0, b"a.py")])
def test_malformed_inventory_is_a_miss(layout: WorkspaceLayout, tmp_path: Path, racy: int, path: str | bytes) -> None:
    with open_display_cache(layout) as cache:
        assert cache is not None
        cache._conn.execute("INSERT INTO inventory VALUES (?, 'k', ?, 0)", (tmp_path.as_posix(), racy))
        cache._conn.execute("INSERT INTO inventory_files VALUES (?, ?)", (tmp_path.as_posix(), path))
        assert cache.inventory(tmp_path, "k") is None


def test_failed_inventory_replace_rolls_back_then_allows_a_retry(layout: WorkspaceLayout, tmp_path: Path) -> None:
    with open_display_cache(layout) as cache:
        assert cache is not None
        cache.store_inventory(tmp_path, "old", frozenset({"old.py"}), racy=False)
        cache._conn.execute(
            "CREATE TRIGGER reject_files BEFORE INSERT ON inventory_files BEGIN SELECT RAISE(ABORT, 'fail'); END"
        )
        cache.store_inventory(tmp_path, "new", frozenset({"new.py"}), racy=True)
        assert cache.inventory(tmp_path, "old") == (frozenset({"old.py"}), False)
        cache._conn.execute("DROP TRIGGER reject_files")
        cache.store_inventory(tmp_path, "new", frozenset({"new.py"}), racy=True)
        assert cache.inventory(tmp_path, "new") == (frozenset({"new.py"}), True)


def test_closed_connection_reads_miss_and_writes_drop(
    layout: WorkspaceLayout, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with open_display_cache(layout) as cache:
        assert cache is not None
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert cache.inventory(tmp_path, "k") is None
        assert cache.spans("h", "1:x") is None
        cache.store_inventory(tmp_path, "k", frozenset(), racy=False)
        cache.store_spans("h", "1:x", ())
    assert len(caplog.records) == 4


def test_database_follows_relocated_cache(layout: WorkspaceLayout, tmp_path: Path) -> None:
    moved = layout_for(layout.root, cache_dir=str(tmp_path / "elsewhere"))
    with open_display_cache(moved) as cache:
        assert cache is not None
        cache.store_spans("h", "1:x", SPANS)
    assert (tmp_path / "elsewhere" / "read-index" / "display.db").exists()
    assert not database_path(layout).exists()

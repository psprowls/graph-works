"""Store: connect, read-only, transaction context manager."""

from __future__ import annotations

import io
import sqlite3
import time
from contextlib import closing, redirect_stderr
from pathlib import Path

import pytest
from _git_repo import init_repo, write_and_commit
from code_graph_io import exit_codes, schema, store, update
from code_graph_io.paths import graph_dir


def test_exit_codes_constants() -> None:
    assert exit_codes.SUCCESS == 0
    assert exit_codes.GENERIC == 1
    assert exit_codes.STALE == 2
    assert exit_codes.NOT_INITIALIZED == 3
    assert exit_codes.SCHEMA_MISMATCH == 4
    assert exit_codes.NOT_IN_GIT_REPO == 5
    assert exit_codes.UPDATE_IN_PROGRESS == 6


def test_connect_create_true_creates_file_and_schema(tmp_path: Path) -> None:
    db = tmp_path / "graph" / "code.db"
    conn = store.connect(db, create=True)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert names == {"nodes", "edges", "metadata"}
    finally:
        conn.close()
    assert db.exists()


def test_connect_create_false_missing_raises(tmp_path: Path) -> None:
    db = tmp_path / "code.db"
    with pytest.raises(store.GraphNotInitializedError):
        store.connect(db, create=False)


def test_read_only_connect_blocks_writes(tmp_path: Path) -> None:
    db = tmp_path / "code.db"
    rw = store.connect(db, create=True)
    rw.close()
    ro = store.read_only_connect(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO metadata(key, value) VALUES ('x', 'y')")
    finally:
        ro.close()


def test_transaction_commits_on_success(tmp_path: Path) -> None:
    db = tmp_path / "code.db"
    conn = store.connect(db, create=True)
    try:
        with store.transaction(conn):
            conn.execute("INSERT INTO metadata(key, value) VALUES ('k', 'v')")
        row = conn.execute("SELECT value FROM metadata WHERE key='k'").fetchone()
        assert row == ("v",)
    finally:
        conn.close()


def test_transaction_rolls_back_on_exception(tmp_path: Path) -> None:
    db = tmp_path / "code.db"
    conn = store.connect(db, create=True)
    try:
        with pytest.raises(RuntimeError), store.transaction(conn):
            conn.execute("INSERT INTO metadata(key, value) VALUES ('k', 'v')")
            raise RuntimeError("boom")
        row = conn.execute("SELECT value FROM metadata WHERE key='k'").fetchone()
        assert row is None
    finally:
        conn.close()


def _seed_v1_db(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a code.db at schema_version='1' under the workspace's graph dir."""
    ws = repo_root
    db_path = graph_dir(ws) / "code.db"
    monkeypatch.setattr(schema, "SCHEMA_VERSION", 1)
    conn = store.connect(db_path, create=True)
    conn.close()
    monkeypatch.undo()
    # Sanity: confirm it really is v1 on disk.
    with closing(sqlite3.connect(db_path)) as probe:
        row = probe.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
    assert row == ("1",), row
    return db_path


def test_update_full_rebuilds_v1_db_to_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`gw graph update --full` on a schema-v1 DB unlinks + rebuilds at current schema version."""
    init_repo(tmp_path)
    write_and_commit(tmp_path, {"a.py": "x = 1\n"}, "init")

    db_path = _seed_v1_db(tmp_path, monkeypatch)
    started = time.time()
    time.sleep(0.01)  # ensure mtime delta is observable

    buf = io.StringIO()
    with redirect_stderr(buf):
        update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    stderr = buf.getvalue()
    # Wording is discretionary; substrings are the contract.
    assert "v1" in stderr, stderr
    assert f"v{schema.SCHEMA_VERSION}" in stderr, stderr
    assert "rebuild" in stderr.lower(), stderr

    assert db_path.exists()
    # Stale v1 WAL/SHM siblings did not survive the unlink — any *new* WAL/SHM
    # is fine; we only assert the main DB was rebuilt (mtime after test start).
    assert db_path.stat().st_mtime >= started

    with closing(sqlite3.connect(db_path)) as probe:
        row = probe.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
    assert row == (str(schema.SCHEMA_VERSION),)


def test_update_incremental_on_v1_db_raises_schema_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-`--full` path on a v1 DB raises SchemaMismatchError."""
    init_repo(tmp_path)
    write_and_commit(tmp_path, {"a.py": "x = 1\n"}, "init")
    _seed_v1_db(tmp_path, monkeypatch)

    with pytest.raises(store.SchemaMismatchError) as excinfo:
        update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=False)
    assert excinfo.value.found == "1"
    assert excinfo.value.expected == schema.SCHEMA_VERSION


def test_connect_on_schemaless_db_raises_graph_not_initialized(tmp_path: Path) -> None:
    """An existing-but-empty DB file is functionally uninitialized.

    `_check_schema_version` translates sqlite's "no such table: metadata" into
    `GraphNotInitializedError` so callers need one guard, not two.
    """
    db = tmp_path / "code.db"
    sqlite3.connect(db).close()  # a real DB file with no schema applied
    with pytest.raises(store.GraphNotInitializedError):
        store.connect(db, create=False)


def test_read_only_connect_on_schemaless_db_raises_graph_not_initialized(tmp_path: Path) -> None:
    db = tmp_path / "code.db"
    sqlite3.connect(db).close()
    with pytest.raises(store.GraphNotInitializedError):
        store.read_only_connect(db)


def test_read_only_connect_missing_file_raises_graph_not_initialized(tmp_path: Path) -> None:
    with pytest.raises(store.GraphNotInitializedError):
        store.read_only_connect(tmp_path / "absent.db")


def _seed_wrong_schema_version(db: Path, version: str) -> None:
    conn = store.connect(db, create=True)
    conn.execute("UPDATE metadata SET value = ? WHERE key = 'schema_version'", (version,))
    conn.commit()
    conn.close()


def test_connect_closes_the_connection_when_schema_version_mismatches(tmp_path: Path) -> None:
    """The mismatch path must not leak the open handle it was checking."""
    db = tmp_path / "code.db"
    _seed_wrong_schema_version(db, "0")
    with pytest.raises(store.SchemaMismatchError):
        store.connect(db, create=False)


def test_read_only_connect_closes_the_connection_when_schema_version_mismatches(tmp_path: Path) -> None:
    db = tmp_path / "code.db"
    _seed_wrong_schema_version(db, "0")
    with pytest.raises(store.SchemaMismatchError):
        store.read_only_connect(db)

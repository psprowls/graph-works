from __future__ import annotations

import sqlite3
from pathlib import Path

import okf_io
import pytest
from okf_ext.readindex import IndexUnavailable, open_index
from okf_ext.readindex.store import PROJECTION_VERSION, SCHEMA_VERSION
from readindex_helpers import tree


def _meta(db: Path) -> dict[str, str]:
    with sqlite3.connect(db) as conn:
        return dict(conn.execute("SELECT key, value FROM meta"))


def test_fresh_open_creates_schema_in_wal(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {"a.md": "x"})
    with open_index(tmp_path / "db" / "i.sqlite", root, fingerprint="f1") as index:
        assert index.rebuilt_reason == "created"
        assert index.connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    meta = _meta(tmp_path / "db" / "i.sqlite")
    assert meta["schema_version"] == str(SCHEMA_VERSION)
    assert meta["projection_version"] == str(PROJECTION_VERSION)
    assert meta["okf_io_version"] == okf_io.__version__
    assert meta["fingerprint"] == "f1" and meta["generation"] == "0"


def test_reopen_reuses(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {})
    db = tmp_path / "i.sqlite"
    open_index(db, root).close()
    with open_index(db, root) as index:
        assert index.rebuilt_reason is None


@pytest.mark.parametrize(
    ("key", "reason"),
    [
        ("schema_version", "schema"),
        ("projection_version", "projection"),
        ("okf_io_version", "okf-io"),
        ("fingerprint", "fingerprint"),
    ],
)
def test_mismatch_resets_in_place(tmp_path: Path, key: str, reason: str) -> None:
    root = tree(tmp_path / "b", {})
    db = tmp_path / "i.sqlite"
    open_index(db, root).close()
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE meta SET value = 'stale' WHERE key = ?", (key,))
        conn.execute("UPDATE meta SET value = '7' WHERE key = 'generation'")
    with open_index(db, root) as index:
        assert index.rebuilt_reason == reason
    assert _meta(db)["generation"] == "8"


def test_pattern_change_resets(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {})
    db = tmp_path / "i.sqlite"
    open_index(db, root, ignore=["a/*"]).close()
    with open_index(db, root, ignore=["b/*"]) as index:
        assert index.rebuilt_reason == "patterns"


def test_truncated_database_is_discarded(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {})
    db = tmp_path / "i.sqlite"
    open_index(db, root).close()
    db.write_bytes(db.read_bytes()[:100])
    with open_index(db, root) as index:
        assert index.rebuilt_reason == "corrupt"


def test_garbage_file_is_discarded(tmp_path: Path) -> None:
    db = tmp_path / "i.sqlite"
    db.write_bytes(b"not a database at all" * 100)
    with open_index(db, tree(tmp_path / "b", {})) as index:
        assert index.rebuilt_reason == "corrupt"


def test_uncreatable_database_is_unavailable(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_bytes(b"")
    with pytest.raises(IndexUnavailable):
        open_index(blocker / "i.sqlite", tree(tmp_path / "b", {}))


def test_schema_contains_all_projection_tables_and_no_body(tmp_path: Path) -> None:
    with open_index(tmp_path / "i.sqlite", tree(tmp_path / "b", {})) as index:
        conn = index.connection
        assert conn.isolation_level is None
        assert conn.execute("PRAGMA synchronous").fetchone() == (1,)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"meta", "members", "tags", "links", "headings", "unreadable_dirs", "pruned", "collisions"}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(members)")}
        assert columns == {
            "id",
            "canonical",
            "kind",
            "size",
            "mtime_ns",
            "ino",
            "ctime_ns",
            "sha256",
            "racy",
            "unreadable",
            "type",
            "title",
            "status",
            "fm_json",
            "fm_exact",
            "parse_error",
            "coercion_failures",
            "body_line_offset",
        }
        assert dict(conn.execute("SELECT key, value FROM meta"))["last_reconcile_ns"] == "0"
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    index.close()  # Closing an already closed index is harmless.


def test_busy_probe_does_not_discard_database(tmp_path: Path) -> None:
    from okf_ext.readindex import IndexBusy

    db = tmp_path / "i.sqlite"
    with sqlite3.connect(db) as owner:
        owner.execute("CREATE TABLE sentinel (value TEXT)")
        owner.commit()
        before = db.read_bytes()
        owner.execute("BEGIN EXCLUSIVE")
        with pytest.raises(IndexBusy):
            open_index(db, tree(tmp_path / "b", {}), busy_timeout_ms=10)
        assert db.read_bytes() == before
        owner.rollback()
        assert owner.execute("SELECT name FROM sqlite_master WHERE name='sentinel'").fetchone()


def test_busy_reset_preserves_generation_and_rows(tmp_path: Path) -> None:
    from okf_ext.readindex import IndexBusy

    db = tmp_path / "i.sqlite"
    root = tree(tmp_path / "b", {})
    open_index(db, root).close()
    with sqlite3.connect(db) as owner:
        owner.execute("INSERT INTO pruned VALUES ('kept')")
        owner.execute("UPDATE meta SET value='stale' WHERE key='schema_version'")
        owner.commit()
        owner.execute("BEGIN IMMEDIATE")
        with pytest.raises(IndexBusy):
            open_index(db, root, busy_timeout_ms=10)
        owner.rollback()
        assert owner.execute("SELECT id FROM pruned").fetchall() == [("kept",)]
    assert _meta(db)["generation"] == "0"


@pytest.mark.parametrize("stale", [False, True])
def test_concurrent_opens_recheck_meta_under_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stale: bool
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    db = tmp_path / "i.sqlite"
    root = tree(tmp_path / "b", {})
    if stale:
        open_index(db, root).close()
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE meta SET value='stale' WHERE key='schema_version'")
    else:
        with sqlite3.connect(db) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
    barrier = Barrier(2)
    original = sqlite3.connect

    class RacingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql == "PRAGMA synchronous=NORMAL":
                barrier.wait(timeout=5)
            return result

    def connect(*args, **kwargs):
        return original(*args, **kwargs, factory=RacingConnection)

    monkeypatch.setattr(sqlite3, "connect", connect)

    def attempt():
        with open_index(db, root) as index:
            return index.rebuilt_reason

    with ThreadPoolExecutor(max_workers=2) as pool:
        reasons = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(reasons, key=str) == sorted([None, "schema" if stale else "created"], key=str)
    assert _meta(db)["generation"] == ("1" if stale else "0")


def test_reset_ddl_failure_rolls_back_every_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "i.sqlite"
    root = tree(tmp_path / "b", {})
    open_index(db, root).close()
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO pruned VALUES ('kept')")
        conn.execute("UPDATE meta SET value='stale' WHERE key='schema_version'")
    original = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.strip().startswith("CREATE TABLE members"):
                raise sqlite3.OperationalError("injected DDL failure")
            return super().execute(sql, parameters)

    monkeypatch.setattr(
        sqlite3, "connect", lambda *args, **kwargs: original(*args, **kwargs, factory=FailingConnection)
    )
    with pytest.raises(IndexUnavailable, match="injected DDL failure"):
        open_index(db, root)
    assert _meta(db)["schema_version"] == "stale"
    with original(db) as conn:
        assert conn.execute("SELECT id FROM pruned").fetchall() == [("kept",)]


def test_wal_must_be_available(tmp_path: Path) -> None:
    with pytest.raises(IndexUnavailable):
        open_index(Path(":memory:"), tree(tmp_path / "b", {}))


def test_first_mismatch_wins_and_reset_clears_rows_in_same_file(tmp_path: Path) -> None:
    db = tmp_path / "i.sqlite"
    root = tree(tmp_path / "b", {})
    assert root.is_dir()
    open_index(db, root).close()
    inode = db.stat().st_ino
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO pruned VALUES ('gone')")
        conn.execute("UPDATE meta SET value='stale' WHERE key IN ('schema_version', 'projection_version', 'patterns')")
    with open_index(db, root) as index:
        assert index.rebuilt_reason == "schema"
        assert index.connection.execute("SELECT * FROM pruned").fetchall() == []
        assert db.stat().st_ino == inode


def test_pattern_sequences_are_snapshotted(tmp_path: Path) -> None:
    import json

    ignore, prune = ["a/*"], ["b/*"]
    db = tmp_path / "i.sqlite"
    with open_index(db, tree(tmp_path / "b", {}), ignore=ignore, prune=prune) as index:
        ignore.append("changed")
        prune.clear()
        assert index.ignore == ("a/*",) and index.prune == ("b/*",)
    assert json.loads(_meta(db)["patterns"]) == {"ignore": ["a/*"], "prune": ["b/*"]}


def test_fresh_schema_failure_is_unavailable_and_leaves_no_partial_ddl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.strip().startswith("CREATE TABLE members"):
                raise sqlite3.OperationalError("injected fresh DDL failure")
            return super().execute(sql, parameters)

    monkeypatch.setattr(
        sqlite3, "connect", lambda *args, **kwargs: original(*args, **kwargs, factory=FailingConnection)
    )
    db = tmp_path / "i.sqlite"
    with pytest.raises(IndexUnavailable, match="injected fresh DDL failure"):
        open_index(db, tree(tmp_path / "b", {}))
    with original(db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_locked_result_code_is_not_corruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from okf_ext.readindex import IndexBusy

    original = sqlite3.connect

    class LockedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql == "PRAGMA quick_check":
                error = sqlite3.OperationalError("schema locked")
                error.sqlite_errorcode = sqlite3.SQLITE_LOCKED
                raise error
            return super().execute(sql, parameters)

    db = tmp_path / "i.sqlite"
    root = tree(tmp_path / "b", {})
    open_index(db, root).close()
    before = db.read_bytes()
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: original(*args, **kwargs, factory=LockedConnection))
    with pytest.raises(IndexBusy, match="schema locked"):
        open_index(db, root)
    assert db.read_bytes() == before

"""Explicit, transactional lifecycle of the disposable read-index database."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import okf_io

from okf_ext.readindex._sql import bindings, configure, execute
from okf_ext.readindex.model import IndexBusy, IndexUnavailable, RebuildReason

SCHEMA_VERSION = 1
PROJECTION_VERSION = 2

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE members (
  id TEXT PRIMARY KEY, canonical TEXT NOT NULL, kind TEXT NOT NULL,
  size INTEGER, mtime_ns INTEGER, ino INTEGER, ctime_ns INTEGER,
  sha256 TEXT, racy INTEGER NOT NULL DEFAULT 0, unreadable TEXT,
  type TEXT, title TEXT, status TEXT, fm_json TEXT, fm_exact INTEGER NOT NULL DEFAULT 1,
  parse_error TEXT, coercion_failures TEXT NOT NULL DEFAULT '[]', body_line_offset INTEGER
) WITHOUT ROWID;
CREATE INDEX members_canonical ON members(canonical);
CREATE INDEX members_type ON members(type);
CREATE TABLE tags (member TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (member, tag)) WITHOUT ROWID;
CREATE INDEX tags_tag ON tags(tag);
CREATE TABLE links (
  source TEXT NOT NULL, ordinal INTEGER NOT NULL, raw TEXT NOT NULL, target TEXT,
  target_canonical TEXT, fragment TEXT, external INTEGER NOT NULL, image INTEGER NOT NULL, line INTEGER,
  PRIMARY KEY (source, ordinal)
) WITHOUT ROWID;
CREATE INDEX links_target ON links(target);
CREATE INDEX links_target_canonical ON links(target_canonical);
CREATE TABLE headings (
  member TEXT NOT NULL, ordinal INTEGER NOT NULL, level INTEGER NOT NULL, text TEXT NOT NULL,
  line INTEGER NOT NULL, quoted INTEGER NOT NULL, PRIMARY KEY (member, ordinal)
) WITHOUT ROWID;
CREATE TABLE unreadable_dirs (id TEXT PRIMARY KEY, reason TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE pruned (id TEXT PRIMARY KEY) WITHOUT ROWID;
CREATE TABLE collisions (canonical TEXT PRIMARY KEY, ids_json TEXT NOT NULL) WITHOUT ROWID;
"""


@dataclass(frozen=True, slots=True, eq=False)
class ReadIndex:
    """An open database and the identity of its bundle projection."""

    db_path: Path
    root: Path
    ignore: tuple[str, ...]
    prune: tuple[str, ...]
    fingerprint: str
    busy_timeout_ms: int
    rebuilt_reason: RebuildReason | None
    connection: sqlite3.Connection
    identity: tuple[tuple[str, str], ...]

    def __enter__(self) -> ReadIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._drop_idle_sidecars()
        finally:
            self.connection.close()

    def _drop_idle_sidecars(self) -> None:
        """Leave no `-wal`/`-shm` behind when this is the only open connection.

        SQLite 3.54.0 keeps both sidecars after the last connection closes, where 3.53.4
        removes them. Truncate the log, then take an exclusive lock: it fails with BUSY
        while any other connection is open, which is the only case where the files are
        still in use. Best effort -- a failure just leaves the sidecars as before.
        """
        connection = self.connection
        try:
            if execute(connection, "PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                return
            execute(connection, "PRAGMA locking_mode=EXCLUSIVE")
            execute(connection, "BEGIN IMMEDIATE")
            execute(connection, "COMMIT")
        except sqlite3.Error:
            return
        for suffix in ("-wal", "-shm"):
            Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)


def _busy(error: sqlite3.Error) -> bool:
    # Extended result codes retain the primary code in the low byte.
    code = getattr(error, "sqlite_errorcode", 0)
    return (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or any(
        phrase in str(error).lower()
        for phrase in ("database is locked", "database table is locked", "database is busy")
    )


def _meta(connection: sqlite3.Connection) -> dict[str, str] | None:
    if execute(connection, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone() is None:
        return None
    return dict(execute(connection, "SELECT key, value FROM meta"))


def _mismatch(meta: dict[str, str], expected: dict[str, str]) -> RebuildReason | None:
    checks: tuple[tuple[str, RebuildReason], ...] = (
        ("schema_version", "schema"),
        ("projection_version", "projection"),
        ("okf_io_version", "okf-io"),
        ("fingerprint", "fingerprint"),
        ("patterns", "patterns"),
    )
    for key, reason in checks:
        if meta.get(key) != expected[key]:
            return reason
    return None


def require_identity(index: ReadIndex) -> None:
    """Fence stale publishers without resetting a newer owner's projection."""
    meta = _meta(index.connection)
    if meta is None or any(meta.get(key) != value for key, value in index.identity):
        raise IndexUnavailable("read-index projection identity changed; reopen the handle")


def _reset(connection: sqlite3.Connection, expected: dict[str, str]) -> RebuildReason | None:
    execute(connection, "BEGIN IMMEDIATE")
    try:
        # The probe was only a hint: another opener may have already rebuilt.
        meta = _meta(connection)
        reason = "created" if meta is None else _mismatch(meta, expected)
        if reason is not None:
            generation = 0 if meta is None else int(meta.get("generation", "0")) + 1
            tables = execute(
                connection, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            for (name,) in tables.fetchall():
                quoted = name.replace('"', '""')
                execute(connection, f'DROP TABLE "{quoted}"')
            # executescript implicitly commits; execute each DDL statement so
            # drops, creates, and metadata remain in one rollback boundary.
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    execute(connection, statement)
            connection.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                (
                    bindings(item)
                    for item in {
                        **expected,
                        "epoch": uuid4().hex,
                        "generation": str(generation),
                        "last_reconcile_ns": "0",
                    }.items()
                ),
            )
        execute(connection, "COMMIT")
        return reason
    except BaseException:
        execute(connection, "ROLLBACK")
        raise


def open_index(
    db_path: Path,
    root: Path,
    *,
    ignore: Sequence[str] = (),
    prune: Sequence[str] = (),
    fingerprint: str = "",
    busy_timeout_ms: int = 5000,
) -> ReadIndex:
    """Open, create, or reset a disposable index, preserving it on contention."""
    ignore_tuple, prune_tuple = tuple(ignore), tuple(prune)
    expected = {
        "schema_version": str(SCHEMA_VERSION),
        "projection_version": str(PROJECTION_VERSION),
        "okf_io_version": okf_io.__version__,
        "fingerprint": fingerprint,
        "patterns": json.dumps({"ignore": list(ignore_tuple), "prune": list(prune_tuple)}),
    }
    connection: sqlite3.Connection | None = None
    corrupt = False
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            db_path, timeout=busy_timeout_ms / 1000, isolation_level=None, check_same_thread=False
        )
        configure(connection)
        try:
            if execute(connection, "PRAGMA quick_check").fetchall() != [("ok",)]:
                raise sqlite3.DatabaseError("quick_check failed")
            meta = _meta(connection)
        except sqlite3.DatabaseError as error:
            if _busy(error):
                raise IndexBusy(str(error)) from error
            connection.close()
            connection = None
            for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
                path.unlink(missing_ok=True)
            connection = sqlite3.connect(
                db_path, timeout=busy_timeout_ms / 1000, isolation_level=None, check_same_thread=False
            )
            configure(connection)
            corrupt, meta = True, None
        if execute(connection, "PRAGMA journal_mode=WAL").fetchone() != ("wal",):
            raise IndexUnavailable("WAL could not be enabled")
        execute(connection, "PRAGMA synchronous=NORMAL")
        reason = _reset(connection, expected) if meta is None or _mismatch(meta, expected) is not None else None
        if corrupt:
            reason = "corrupt"
        identity = tuple(
            {**expected, "epoch": dict(execute(connection, "SELECT key, value FROM meta"))["epoch"]}.items()
        )
        return ReadIndex(
            db_path, root, ignore_tuple, prune_tuple, fingerprint, busy_timeout_ms, reason, connection, identity
        )
    except (OSError, sqlite3.Error, ValueError) as error:
        if connection is not None:
            connection.close()
        if isinstance(error, sqlite3.Error) and _busy(error):
            raise IndexBusy(str(error)) from error
        raise IndexUnavailable(str(error)) from error
    except BaseException:
        if connection is not None:
            connection.close()
        raise

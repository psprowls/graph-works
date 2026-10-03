"""Disposable repository inventories and extracted citation spans for display.

The cache only skips work; it never decides a result. Deleting it loses nothing,
and ``gw util read-index --rebuild`` does not touch it. This is workspace layer 0
because ``repo_files`` calls it (D-004); extractor versions come from callers to
avoid importing the citation grammar back into its dependency.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from time import time_ns

from code_graph_io import __version__ as _IGNORE_VERSION

from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import checked_bool

DB_NAME = "display.db"
SCHEMA_VERSION = "1"
BUSY_TIMEOUT_MS = 5000
# read_session sits above workspace, so its constant cannot be imported here.
ENABLED_KEY = "read_index.enabled"
logger = logging.getLogger("graph_works_core.display_cache")

Span = tuple[str, int, str, int, int]

_TABLES = {
    "meta": "key, value",
    "inventory": "root, key_json, racy, captured_ns",
    "inventory_files": "root, path",
    "citation_spans": "sha256, extractor, spans_json",
}
_SCHEMA = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE inventory (root TEXT PRIMARY KEY, key_json TEXT NOT NULL, "
    "racy INTEGER NOT NULL, captured_ns INTEGER NOT NULL)",
    "CREATE TABLE inventory_files (root TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY (root, path))",
    "CREATE TABLE citation_spans (sha256 TEXT PRIMARY KEY, extractor TEXT NOT NULL, spans_json TEXT NOT NULL)",
)


class _Stale(Exception):
    """A version change discards derived data silently."""


def database_path(layout: WorkspaceLayout) -> Path:
    """Return the display database location without creating it."""
    return layout.cache_dir / "read-index" / DB_NAME


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except sqlite3.Error:
        # Preserve the original failure and its single warning.
        with suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _check(conn: sqlite3.Connection) -> None:
    tables = _table_names(conn)
    if not tables:
        with _transaction(conn):
            # A second opener may have initialized while we awaited the lock.
            if _table_names(conn):
                _check(conn)
            else:
                for statement in _SCHEMA:
                    conn.execute(statement)
                conn.executemany(
                    "INSERT INTO meta VALUES (?, ?)",
                    (("schema_version", SCHEMA_VERSION), ("ignore_version", _IGNORE_VERSION)),
                )
        return
    if "meta" not in tables:
        raise sqlite3.DatabaseError("display schema is missing meta")
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
    except sqlite3.OperationalError as exc:
        if exc.sqlite_errorcode != sqlite3.SQLITE_ERROR:
            raise
        raise sqlite3.DatabaseError("display schema has malformed meta") from exc
    if meta.get("schema_version") != SCHEMA_VERSION or meta.get("ignore_version") != _IGNORE_VERSION:
        raise _Stale("display cache versions changed")
    for table, columns in _TABLES.items():
        if table not in tables:
            raise sqlite3.DatabaseError(f"display schema is missing {table}")
        try:
            conn.execute(f"SELECT {columns} FROM {table} LIMIT 0")
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode != sqlite3.SQLITE_ERROR:
                raise
            raise sqlite3.DatabaseError(f"display schema has malformed {table}") from exc


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        _check(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _corrupt(exc: sqlite3.DatabaseError) -> bool:
    # Operational errors include busy, locked, read-only and disk-full databases.
    # Only actual corruption justifies unlinking an operationally unavailable DB.
    return not isinstance(exc, sqlite3.OperationalError) or (getattr(exc, "sqlite_errorcode", 0) & 0xFF) in (
        sqlite3.SQLITE_CORRUPT,
        sqlite3.SQLITE_NOTADB,
    )


@contextmanager
def open_display_cache(layout: WorkspaceLayout) -> Iterator[DisplayCache | None]:
    """Open a disposable display cache, falling back without hiding manifest errors."""
    try:
        enabled = checked_bool(layout, ENABLED_KEY)
    except OSError:
        enabled = False
    if not enabled:
        yield None
        return
    db = database_path(layout)
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = _connect(db)
        except (_Stale, sqlite3.DatabaseError) as exc:
            if isinstance(exc, sqlite3.DatabaseError) and not _corrupt(exc):
                raise
            for path in (db, Path(f"{db}-wal"), Path(f"{db}-shm")):
                path.unlink(missing_ok=True)
            if not isinstance(exc, _Stale):
                logger.warning("display cache discarded (%s): %s", exc, db)
            conn = _connect(db)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("display cache unavailable (%s): %s", db, exc)
        yield None
        return
    cache = DisplayCache(conn, db)
    try:
        yield cache
    finally:
        cache.close()


class DisplayCache:
    """A connection scoped to one display operation; failed accesses are misses."""

    def __init__(self, conn: sqlite3.Connection, db: Path) -> None:
        self._conn = conn
        self._db = db

    def _warn(self, op: str, exc: sqlite3.Error) -> None:
        logger.warning("display cache %s failed (%s): %s", op, self._db, exc)

    def inventory(self, root: Path, key: str) -> tuple[frozenset[str], bool] | None:
        """Return a validated inventory only when its key matches."""
        try:
            # One statement pins the key and files to the same SQLite snapshot.
            rows = self._conn.execute(
                "SELECT i.racy, f.path FROM inventory AS i "
                "LEFT JOIN inventory_files AS f ON f.root=i.root WHERE i.root=? AND i.key_json=?",
                (root.as_posix(), key),
            ).fetchall()
            if not rows or type(rows[0][0]) is not int or rows[0][0] not in (0, 1):
                return None
            paths = [path for _, path in rows if path is not None]
            if any(not isinstance(path, str) or not path for path in paths):
                return None
            return frozenset(paths), bool(rows[0][0])
        except sqlite3.Error as exc:
            self._warn("inventory", exc)
            return None

    def store_inventory(self, root: Path, key: str, files: frozenset[str], *, racy: bool) -> None:
        """Replace the key and all files atomically; failed writes are dropped."""
        root_text = root.as_posix()
        try:
            with _transaction(self._conn):
                self._conn.execute("DELETE FROM inventory_files WHERE root=?", (root_text,))
                self._conn.execute(
                    "INSERT OR REPLACE INTO inventory VALUES (?, ?, ?, ?)", (root_text, key, int(racy), time_ns())
                )
                self._conn.executemany(
                    "INSERT INTO inventory_files VALUES (?, ?)", ((root_text, path) for path in files)
                )
        except sqlite3.Error as exc:
            self._warn("store inventory", exc)

    def spans(self, sha256: str, extractor: str) -> tuple[Span, ...] | None:
        """Return validated spans for the content hash and extractor version."""
        try:
            row = self._conn.execute(
                "SELECT extractor, spans_json FROM citation_spans WHERE sha256=?", (sha256,)
            ).fetchone()
        except sqlite3.Error as exc:
            self._warn("spans", exc)
            return None
        if row is None or row[0] != extractor or not isinstance(row[1], str):
            return None
        try:
            entries = json.loads(row[1])
        except (ValueError, RecursionError):
            return None
        if not isinstance(entries, list):
            return None
        spans: list[Span] = []
        for entry in entries:
            if not isinstance(entry, list) or len(entry) != 5:
                return None
            raw, line, path, start, end = entry
            if (
                not isinstance(raw, str)
                or type(line) is not int
                or not isinstance(path, str)
                or type(start) is not int
                or type(end) is not int
                or line < 1
                or start < 1
                or end < 1
            ):
                return None
            spans.append((raw, line, path, start, end))
        return tuple(spans)

    def store_spans(self, sha256: str, extractor: str, spans: tuple[Span, ...]) -> None:
        """Replace spans atomically without letting cache failures affect display."""
        text = json.dumps([list(span) for span in spans], separators=(",", ":"), ensure_ascii=False)
        try:
            with _transaction(self._conn):
                self._conn.execute("INSERT OR REPLACE INTO citation_spans VALUES (?, ?, ?)", (sha256, extractor, text))
        except sqlite3.Error as exc:
            self._warn("store spans", exc)

    def close(self) -> None:
        """Release the connection."""
        self._conn.close()

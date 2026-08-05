"""SQLite store: connect, read-only connect, transaction context manager."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from code_graph_io import schema


class GraphNotInitializedError(Exception):
    """Raised when connect(create=False) is called on a missing DB file."""


class SchemaMismatchError(Exception):
    """Raised when the on-disk schema_version doesn't match schema.SCHEMA_VERSION."""

    def __init__(self, found: str | None, expected: int) -> None:
        self.found = found
        self.expected = expected
        super().__init__(
            f"graph schema version mismatch: found {found!r}, expected {expected}; "
            "run `gw graph update --full` to rebuild"
        )


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")


def _check_schema_version(conn: sqlite3.Connection) -> None:
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError as exc:
        # A DB file that exists but has never had the schema applied (e.g. an
        # empty placeholder file) is functionally uninitialized, same as a
        # missing file — surface it as GraphNotInitializedError, not a raw
        # sqlite error, so callers only need one guard.
        if "no such table" in str(exc):
            raise GraphNotInitializedError(f"graph DB has no schema applied yet: {exc}") from exc
        raise
    found = row[0] if row else None
    if found != str(schema.SCHEMA_VERSION):
        raise SchemaMismatchError(found=found, expected=schema.SCHEMA_VERSION)


def connect(db_path: Path, *, create: bool = False, busy_timeout_ms: int | None = None) -> sqlite3.Connection:
    """Open a read-write connection to the code graph SQLite DB.

    If `create=True` and the DB file doesn't exist, parent dirs are
    created and the schema is applied. If `create=False` and the file
    is missing, raises `GraphNotInitializedError`.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        if not create:
            raise GraphNotInitializedError(
                f"graph DB not found at {db_path}; run `gw graph update --full` to initialize"
            )
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    if busy_timeout_ms is not None:
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    _apply_pragmas(conn)
    if create:
        schema.apply_schema(conn)
    else:
        try:
            _check_schema_version(conn)
        except SchemaMismatchError:
            conn.close()
            raise
    return conn


def read_only_connect(db_path: Path) -> sqlite3.Connection:
    """Open a read-only connection. Writes raise sqlite3.OperationalError."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise GraphNotInitializedError(f"graph DB not found at {db_path}; run `gw graph update --full` to initialize")
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    try:
        _check_schema_version(conn)
    except SchemaMismatchError:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Wrap a write block in a single transaction. Rolls back on exception."""
    try:
        conn.execute("BEGIN")
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()

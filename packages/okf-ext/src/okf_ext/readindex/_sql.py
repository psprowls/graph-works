"""Lossless SQLite bindings for the public reader's Python strings.

Ordinary strings stay TEXT. Strings containing surrogate code points use
UTF-8 surrogatepass BLOBs; SQLite's native TEXT binder rejects those strings.
Only this index's connection decodes these BLOBs back to strings. No binary
application columns exist in this disposable projection.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable


def bindings(values: Iterable[object]) -> tuple[object, ...]:
    result: list[object] = []
    for value in values:
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                value = value.encode("utf-8", errors="surrogatepass")
        result.append(value)
    return tuple(result)


def _text(value: bytes) -> str:
    return value.decode("utf-8", errors="surrogatepass")


def _row(cursor: sqlite3.Cursor, values: tuple[object, ...]) -> tuple[object, ...]:
    return tuple(_text(value) if isinstance(value, bytes) else value for value in values)


def configure(connection: sqlite3.Connection) -> None:
    # JSON SQL functions can return TEXT derived from a BLOB-backed fm_json.
    connection.text_factory = _text
    connection.row_factory = _row


def execute(connection: sqlite3.Connection, sql: str, parameters: Iterable[object] = ()) -> sqlite3.Cursor:
    return connection.execute(sql, bindings(parameters))

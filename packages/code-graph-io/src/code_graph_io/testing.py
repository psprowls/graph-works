"""Sanctioned test-only DB access. Imported by tests, never by production code.

``open_store`` -> a :class:`GraphStore` on an arbitrary db path (for building
graphs through the public handle). ``raw_conn`` -> a raw read-write
``sqlite3.Connection`` for bulk-INSERT fixture seeding — the single blessed
escape hatch so conftests stop calling ``sqlite3.connect`` directly.

Both take a bare file path (NOT a workspace), unlike ``open_reader`` /
``open_writer``; schema is applied when ``create=True`` and the file is new.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from code_graph_io import store as _store
from code_graph_io.handle import GraphStore


def open_store(db_path: Path, *, create: bool = True) -> GraphStore:
    """Return a writable :class:`GraphStore` on an arbitrary db path.

    Applies the schema when ``create=True`` and the file does not yet exist.
    """
    return GraphStore(_store.connect(Path(db_path), create=create))


def raw_conn(db_path: Path, *, create: bool = True) -> sqlite3.Connection:
    """Return a raw read-write ``sqlite3.Connection`` for bulk fixture seeding.

    Applies the schema when ``create=True`` and the file does not yet exist.
    """
    return _store.connect(Path(db_path), create=create)

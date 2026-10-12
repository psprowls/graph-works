"""Tests for the sanctioned test-only DB access seam (code_graph_io.testing)."""

from __future__ import annotations

from pathlib import Path

import pytest
from code_graph_io import GraphNotInitializedError, GraphStore
from code_graph_io import testing as gtesting


def test_open_store_creates_and_returns_store(tmp_path: Path):
    db = tmp_path / "code.db"
    store = gtesting.open_store(db, create=True)
    try:
        assert isinstance(store, GraphStore)
        # GraphStore exposes no node_count() — assert via raw conn instead:
        assert store._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0
    finally:
        store.close()


def test_raw_conn_round_trips_a_node(tmp_path: Path):
    db = tmp_path / "code.db"
    conn = gtesting.raw_conn(db)
    try:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO nodes (kind, name, path) VALUES ('package','demo','pkg/demo')")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
    finally:
        conn.close()


def test_raw_graph_conn_creates_schema_in_graph_directory(tmp_path: Path):
    graph_dir = tmp_path / "nested" / "graph"
    conn = gtesting.raw_graph_conn(graph_dir=graph_dir)
    try:
        assert Path(conn.execute("PRAGMA database_list").fetchone()[2]) == graph_dir / "code.db"
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"nodes", "edges", "metadata"} <= tables
        assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0
    finally:
        conn.close()


def test_raw_graph_conn_reopens_same_database_without_creation(tmp_path: Path):
    conn = gtesting.raw_conn(tmp_path / "code.db")
    try:
        with conn:
            conn.execute("INSERT INTO nodes (kind, name, path) VALUES ('package','demo','pkg/demo')")
    finally:
        conn.close()
    reopened = gtesting.raw_graph_conn(graph_dir=tmp_path, create=False)
    try:
        assert reopened.execute("SELECT kind, name, path FROM nodes").fetchall() == [("package", "demo", "pkg/demo")]
    finally:
        reopened.close()


def test_raw_graph_conn_without_creation_refuses_missing_database(tmp_path: Path):
    graph_dir = tmp_path / "missing"
    with pytest.raises(GraphNotInitializedError):
        gtesting.raw_graph_conn(graph_dir=graph_dir, create=False)
    assert not graph_dir.exists()

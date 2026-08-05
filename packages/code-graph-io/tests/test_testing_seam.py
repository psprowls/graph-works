"""Tests for the sanctioned test-only DB access seam (code_graph_io.testing)."""

from __future__ import annotations

from pathlib import Path

from code_graph_io import GraphStore
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

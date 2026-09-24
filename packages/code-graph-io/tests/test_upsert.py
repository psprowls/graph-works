"""Upsert: GraphRecords → SQLite rows; tuple-key dedup; attrs_json round-trip."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from code_graph_io import store, upsert
from code_graph_io.records import GraphEdge, GraphNode, GraphRecords


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "code.db"
    c = store.connect(db, create=True)
    yield c
    c.close()


def _records(nodes=(), edges=()) -> GraphRecords:
    return GraphRecords(nodes=tuple(nodes), edges=tuple(edges))


def test_upsert_inserts_nodes(conn: sqlite3.Connection) -> None:
    records = _records(
        nodes=[
            GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
            GraphNode(kind="function", name="foo", path="a.py", line=10, attrs={"is_async": True}),
        ],
    )
    upsert.upsert_records(conn, records)
    rows = conn.execute("SELECT kind, name, path, line FROM nodes ORDER BY kind, name").fetchall()
    assert rows == [
        ("file", "a.py", "a.py", None),
        ("function", "foo", "a.py", 10),
    ]


def test_upsert_is_idempotent_on_tuple_key(conn: sqlite3.Connection) -> None:
    records = _records(
        nodes=[GraphNode(kind="function", name="foo", path="a.py", line=10, attrs={})],
    )
    upsert.upsert_records(conn, records)
    upsert.upsert_records(conn, records)
    count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    assert count == 1


def test_upsert_attrs_json_round_trip(conn: sqlite3.Connection) -> None:
    records = _records(
        nodes=[
            GraphNode(kind="function", name="foo", path="a.py", line=10, attrs={"is_async": True, "decorators": ["x"]})
        ],
    )
    upsert.upsert_records(conn, records)
    row = conn.execute("SELECT attrs_json FROM nodes WHERE name='foo'").fetchone()
    assert json.loads(row[0]) == {"is_async": True, "decorators": ["x"]}


def test_upsert_creates_edges(conn: sqlite3.Connection) -> None:
    records = _records(
        nodes=[
            GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
            GraphNode(kind="function", name="foo", path="a.py", line=10, attrs={}),
        ],
        edges=[
            GraphEdge(
                src=("file", "a.py", "a.py"),
                dst=("function", "foo", "a.py"),
                kind="contains",
                attrs={},
            ),
        ],
    )
    upsert.upsert_records(conn, records)
    edges = conn.execute(
        "SELECT n1.name, n2.name, e.kind FROM edges e JOIN nodes n1 ON e.src=n1.id JOIN nodes n2 ON e.dst=n2.id"
    ).fetchall()
    assert edges == [("a.py", "foo", "contains")]


def test_upsert_unresolved_edge_creates_placeholder(conn: sqlite3.Connection) -> None:
    records = _records(
        nodes=[GraphNode(kind="function", name="caller", path="a.py", line=1, attrs={})],
        edges=[
            GraphEdge(
                src=("function", "caller", "a.py"),
                dst=("function", "missing", None),
                kind="calls",
                attrs={},
            ),
        ],
    )
    upsert.upsert_records(conn, records)
    placeholder = conn.execute("SELECT kind, name, path FROM nodes WHERE name='missing'").fetchone()
    assert placeholder == ("unresolved_symbol", "missing", None)
    edge = conn.execute("SELECT attrs_json FROM edges WHERE kind='calls'").fetchone()
    assert json.loads(edge[0]) == {
        "resolution": "unresolved",
        "symbol_kind": "function",
    }


def test_upsert_uri_lands_in_column(conn: sqlite3.Connection) -> None:
    """PITFALL 4 lock — uri lands in column, not attrs_json."""
    records = _records(
        nodes=[
            GraphNode(
                kind="package",
                name="auth",
                path="packages/auth",
                line=None,
                attrs={"uri": "pkg:org/repo/auth", "version": "1.0", "language": "python"},
            ),
        ],
    )
    upsert.upsert_records(conn, records)
    uri_row = conn.execute("SELECT uri FROM nodes WHERE kind='package' AND name='auth'").fetchone()
    assert uri_row == ("pkg:org/repo/auth",)
    attrs_row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='package' AND name='auth'").fetchone()
    parsed = json.loads(attrs_row[0])
    assert "uri" not in parsed
    assert parsed == {"version": "1.0", "language": "python"}


def test_upsert_node_without_uri_has_null_uri_column(conn: sqlite3.Connection) -> None:
    """Regression guard: nodes without a uri attr leave the uri column NULL."""
    records = _records(
        nodes=[
            GraphNode(
                kind="function",
                name="bar",
                path="b.py",
                line=5,
                attrs={"is_async": False},
            ),
        ],
    )
    upsert.upsert_records(conn, records)
    row = conn.execute("SELECT uri, attrs_json FROM nodes WHERE name='bar'").fetchone()
    assert row[0] is None
    assert json.loads(row[1]) == {"is_async": False}


def test_dependency_reidentified_by_uri_across_path_format_change(conn: sqlite3.Connection) -> None:
    """Dependency identity anchors to URI, not the synthetic path.

    A path-format change must update the existing dependency row in place rather
    than forking a second node that shares the live node's URI (the orphan-dup
    root cause behind the index-page double-listing bug).
    """
    uri = "dependency:test/repo/pypi/deepeval"
    old = GraphNode(
        kind="dependency",
        name="deepeval",
        path="",
        line=None,
        attrs={"uri": uri, "ecosystem": "pypi", "name": "deepeval"},
    )
    upsert.upsert_records(conn, _records(nodes=[old]))
    # same dependency, new synthetic path format, SAME uri
    new = GraphNode(
        kind="dependency",
        name="deepeval",
        path="dependency:test/repo:pypi:deepeval",
        line=None,
        attrs={"uri": uri, "ecosystem": "pypi", "name": "deepeval"},
    )
    upsert.upsert_records(conn, _records(nodes=[new]))

    rows = conn.execute("SELECT path FROM nodes WHERE kind='dependency' AND uri=?", (uri,)).fetchall()
    assert len(rows) == 1  # today: 2 (forked) → FAILS
    assert rows[0][0] == "dependency:test/repo:pypi:deepeval"  # path updated in place


def test_upsert_uri_idempotent(conn: sqlite3.Connection) -> None:
    """Re-upserting the same uri-bearing node preserves uri without duplicating."""
    records = _records(
        nodes=[
            GraphNode(
                kind="package",
                name="auth",
                path="packages/auth",
                line=None,
                attrs={"uri": "pkg:org/repo/auth"},
            ),
        ],
    )
    upsert.upsert_records(conn, records)
    upsert.upsert_records(conn, records)
    count = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='package' AND name='auth'").fetchone()[0]
    assert count == 1
    uri_row = conn.execute("SELECT uri FROM nodes WHERE kind='package' AND name='auth'").fetchone()
    assert uri_row == ("pkg:org/repo/auth",)


def test_current_repo_does_not_leak_to_a_connection_reusing_a_freed_id() -> None:
    """A never-cleared scope must stay bound to its own connection object.

    CPython reuses a freed object's address, so a registry keyed by bare
    `id(conn)` hands a stale member scope to whichever later connection lands
    at that address — stamping its rows with a foreign `repo`.
    """
    leaked = sqlite3.connect(":memory:")
    upsert.set_current_repo(leaked, "repo:acme/stale")
    leaked.close()
    del leaked

    fresh = [sqlite3.connect(":memory:") for _ in range(200)]
    try:
        assert all(upsert._current_repo(c) is None for c in fresh)
    finally:
        for c in fresh:
            c.close()

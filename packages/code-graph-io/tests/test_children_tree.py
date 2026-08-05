"""children_tree / default_child_depth: kind-dispatched containment traversal."""

from __future__ import annotations

import sqlite3

from code_graph_io import queries
from code_graph_io.schema import apply_schema


def test_default_child_depth_mapping() -> None:
    for kind in ("repository", "package", "app"):
        assert queries.default_child_depth(kind) == 1
    for kind in ("file", "class", "function", "method", "type", "test_suite", "subpackage"):
        assert queries.default_child_depth(kind) == 2


def test_childnode_is_frozen_with_default_children() -> None:
    c = queries.ChildNode(kind="file", uri=None, path="a.py", line=None)
    assert c.children == []
    import dataclasses

    assert dataclasses.is_dataclass(c)


def _node_record(conn: sqlite3.Connection, kind: str, name: str) -> queries.NodeRecord:
    row = conn.execute(
        "SELECT kind, name, path, line, attrs_json, uri FROM nodes WHERE kind=? AND name=?",
        (kind, name),
    ).fetchone()
    assert row is not None, f"seeded graph missing {kind} {name!r}"
    return queries._row_to_node(row)


def _node_id(conn: sqlite3.Connection, kind: str, name: str) -> int:
    return conn.execute("SELECT id FROM nodes WHERE kind=? AND name=?", (kind, name)).fetchone()[0]


def test_package_or_app_depth1_direct_children(seeded_db: sqlite3.Connection) -> None:
    # mypkg is a top-level workspace member; assert on its real seeded kind.
    row = seeded_db.execute("SELECT kind FROM nodes WHERE name='mypkg' AND kind IN ('package','app')").fetchone()
    assert row is not None
    kind = row[0]
    rec = _node_record(seeded_db, kind, "mypkg")
    tree = queries.children_tree(seeded_db, node=rec, depth=1)

    # Every direct child is unexpanded at depth 1.
    assert all(c.children == [] for c in tree)
    child_kinds = {c.kind for c in tree}
    # Structural children (subpackage/file) + entry_point + test_suite, no external deps.
    assert "subpackage" in child_kinds or "file" in child_kinds
    assert "test_suite" in child_kinds  # via incoming `tests`, not double-counted
    assert "entry_point" in child_kinds
    # External dependencies are NOT children.
    assert "dependency" not in child_kinds
    # test_suite count matches the `tests` edge count exactly (no physically_contains double-count).
    expected_suite_count = seeded_db.execute(
        "SELECT COUNT(*) FROM edges e JOIN nodes ts ON e.src=ts.id "
        "WHERE e.kind='tests' AND ts.kind='test_suite' AND e.dst = "
        "(SELECT id FROM nodes WHERE name=? AND kind=?)",
        ("mypkg", kind),
    ).fetchone()[0]
    assert sum(1 for c in tree if c.kind == "test_suite") == expected_suite_count


def test_subpackage_to_file_to_symbol_boundary(seeded_db: sqlite3.Connection) -> None:
    rec = _node_record(seeded_db, "subpackage", "mypkg.sub")
    # depth 1: subpackage's direct children (nested subpackage + files), unexpanded.
    d1 = queries.children_tree(seeded_db, node=rec, depth=1)
    assert d1, "mypkg.sub should physically_contain children"
    assert all(c.children == [] for c in d1)
    assert {c.kind for c in d1} <= {"subpackage", "file"}
    # depth 2: files expand to their `contains` symbols (or stay empty if none).
    d2 = queries.children_tree(seeded_db, node=rec, depth=2)
    # the nested subpackage mypkg.sub.deep expands to its files at depth 2
    deep = [c for c in d2 if c.kind == "subpackage"]
    if deep:
        assert any(gc.kind == "file" for gc in deep[0].children)


def test_file_children_are_symbols_via_contains(seeded_db: sqlite3.Connection) -> None:
    # Find a file that `contains` at least one symbol.
    fid_row = seeded_db.execute(
        "SELECT s.path FROM edges e JOIN nodes s ON e.src=s.id JOIN nodes d ON e.dst=d.id "
        "WHERE e.kind='contains' AND s.kind='file' AND d.kind IN ('function','class','method','type') LIMIT 1"
    ).fetchone()
    assert fid_row is not None, "sample_monorepo should have a file containing a symbol"
    path = fid_row[0]
    rec = queries._row_to_node(
        seeded_db.execute(
            "SELECT kind,name,path,line,attrs_json,uri FROM nodes WHERE kind='file' AND path=?", (path,)
        ).fetchone()
    )
    tree = queries.children_tree(seeded_db, node=rec, depth=1)
    assert tree and all(c.kind in {"function", "class", "method", "type"} for c in tree)


def test_leaf_kinds_return_empty(seeded_db: sqlite3.Connection) -> None:
    # A dependency node: pick any.
    dep = seeded_db.execute(
        "SELECT kind,name,path,line,attrs_json,uri FROM nodes WHERE kind='dependency' LIMIT 1"
    ).fetchone()
    if dep is not None:
        rec = queries._row_to_node(dep)
        assert queries.children_tree(seeded_db, node=rec, depth=3) == []


def test_unknown_node_returns_empty(seeded_db: sqlite3.Connection) -> None:
    ghost = queries.NodeRecord(kind="package", name="does-not-exist", path=None, line=None, attrs={})
    assert queries.children_tree(seeded_db, node=ghost, depth=2) == []


def test_children_for_resolves_default_depth(seeded_db: sqlite3.Connection) -> None:
    row = seeded_db.execute("SELECT kind FROM nodes WHERE name='mypkg' AND kind IN ('package','app')").fetchone()
    kind = row[0]
    _tree, eff = queries.children_for(seeded_db, kind=kind, name="mypkg", depth=None)
    assert eff == queries.default_child_depth(kind) == 1
    _tree2, eff2 = queries.children_for(seeded_db, kind=kind, name="mypkg", depth=2)
    assert eff2 == 2


def test_visited_set_terminates_on_cycle() -> None:
    """A physically_contains cycle A->B->A must not recurse infinitely.

    The seeded graph is acyclic, so build a tiny in-memory graph with the real
    schema and a deliberate cycle to exercise the `visited` backstop in _expand.
    """
    conn = sqlite3.connect(":memory:")
    apply_schema(conn)
    conn.execute(
        "INSERT INTO nodes (id, kind, name, path, line, attrs_json, uri) VALUES (?,?,?,?,?,?,?)",
        (1, "package", "A", None, None, None, "pkg:A"),
    )
    conn.execute(
        "INSERT INTO nodes (id, kind, name, path, line, attrs_json, uri) VALUES (?,?,?,?,?,?,?)",
        (2, "subpackage", "B", None, None, None, "subpkg:B"),
    )
    # Deliberate cycle: A physically_contains B, and B physically_contains A.
    conn.execute("INSERT INTO edges (src, dst, kind, attrs_json) VALUES (1, 2, 'physically_contains', NULL)")
    conn.execute("INSERT INTO edges (src, dst, kind, attrs_json) VALUES (2, 1, 'physically_contains', NULL)")

    rec = queries.NodeRecord(kind="package", name="A", path=None, line=None, attrs={"uri": "pkg:A"})
    # depth=5 would recurse forever without the visited backstop.
    tree = queries.children_tree(conn, node=rec, depth=5)
    conn.close()

    # A's only child is B; B would re-point at A, but A is in `visited`, so B
    # has no further expansion. The result is bounded (one node, no grandchildren).
    assert len(tree) == 1
    assert tree[0].kind == "subpackage" and tree[0].uri == "subpkg:B"
    assert tree[0].children == []

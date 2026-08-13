"""Upsert GraphRecords into SQLite. Tuple-keyed; idempotent."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from code_graph_io.records import GraphEdge, GraphNode, GraphRecords

NodeKey = tuple[str, str, str | None]
# Ecosystem-global node kinds: identity is never repo-scoped and they are never
# stamped with a member repo (mirrors update.py's end-of-pass stamp exclusion).
# Their synthetic path is non-None, so without this they'd be treated like a
# path-bearing member node and forked per repo.
_GLOBAL_KINDS = frozenset({"builtin", "dependency"})
_UNRESOLVED_SYMBOL_KIND = "unresolved_symbol"
_SYMBOL_PLACEHOLDER_KINDS = frozenset({"function", "method", "class", "type"})
_SYMBOL_PLACEHOLDER_EDGE_KINDS = frozenset({"calls", "exports"})


def _serialize(attrs: dict[str, Any]) -> str | None:
    return json.dumps(attrs, sort_keys=True) if attrs else None


# Connection-scoped current member repo URI (sqlite3.Connection forbids
# arbitrary attributes, so key by id(conn)). Set/cleared by set_current_repo.
_CURRENT_REPO: dict[int, str] = {}


def _current_repo(conn: sqlite3.Connection) -> str | None:
    """Connection-scoped current member repo URI, or None for single-repo.

    Multi-repo: set by `update._update_one_repo` for the duration of
    one member's pipeline via `set_current_repo`. When set, path-bearing node
    identity is scoped by `repo` so two sibling repos that share a relative
    path (e.g. both have `pyproject.toml`) materialize as DISTINCT nodes
    instead of merging into one (which would give it two physically_contains
    parents and violate the strict-tree invariant). None → identical to the
    historical single-repo behavior.
    """
    return _CURRENT_REPO.get(id(conn))


def set_current_repo(conn: sqlite3.Connection, repo_uri: str | None) -> None:
    """Set (or clear) the connection-scoped current member repo URI."""
    if repo_uri is None:
        _CURRENT_REPO.pop(id(conn), None)
    else:
        _CURRENT_REPO[id(conn)] = repo_uri


def _node_id(conn: sqlite3.Connection, key: NodeKey) -> int | None:
    kind, name, path = key
    if path is None:
        row = conn.execute(
            "SELECT id FROM nodes WHERE kind=? AND name=? AND path IS NULL",
            (kind, name),
        ).fetchone()
        return row[0] if row else None
    current_repo = _current_repo(conn)
    if current_repo is None or kind in _GLOBAL_KINDS:
        # Single-repo, OR an ecosystem-global kind (builtin/dependency): identity
        # is never repo-scoped. Global kinds carry a synthetic non-None path but
        # are shared across all members (repo IS NULL), so they must resolve to
        # the one global row regardless of which member is currently active.
        row = conn.execute(
            "SELECT id FROM nodes WHERE kind=? AND name=? AND path=?",
            (kind, name, path),
        ).fetchone()
    else:
        # A path-bearing member node is owned by the member that produced it. In
        # normal runs path-bearing nodes are stamped at insert (`_insert_node`)
        # once `set_current_repo` is active, so they already carry this member's
        # `repo`. The `OR repo IS NULL` is a defensive fallback for the rare
        # node only stamped by the end-of-pass UPDATE. Either way, never match a
        # SIBLING member's already-stamped row.
        row = conn.execute(
            "SELECT id FROM nodes WHERE kind=? AND name=? AND path=? AND (repo=? OR repo IS NULL)",
            (kind, name, path, current_repo),
        ).fetchone()
    return row[0] if row else None


def _insert_node(
    conn: sqlite3.Connection,
    key: NodeKey,
    line: int | None,
    attrs_json: str | None,
    uri: str | None,
) -> int:
    kind, name, path = key
    # Stamp the current member repo at insert time for path-bearing member nodes
    # so a later sibling member can't merge into this row. Pathless nodes
    # (unresolved symbols) and ecosystem-global kinds (builtin, dependency —
    # which carry a synthetic non-None path) stay repo=NULL.
    repo = _current_repo(conn) if (path is not None and kind not in _GLOBAL_KINDS) else None
    cursor = conn.execute(
        "INSERT INTO nodes(kind, name, path, line, attrs_json, uri, repo) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, name, path, line, attrs_json, uri, repo),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a row id for inserted graph node")
    return cursor.lastrowid


def _dependency_id_by_uri(conn: sqlite3.Connection, uri: str | None) -> int | None:
    """Resolve an existing dependency row by its stable URI.

    A dependency's `path` is a synthetic 1:1 function of (ecosystem, name) — the
    same identity the URI already carries. Keying identity on the URI lets a
    path-format change update the row in place instead of forking a duplicate
    (orphan) node that shares the live node's URI.
    """
    if uri is None:
        return None
    row = conn.execute("SELECT id FROM nodes WHERE kind='dependency' AND uri=?", (uri,)).fetchone()
    return row[0] if row else None


def _upsert_node(conn: sqlite3.Connection, node: GraphNode) -> int:
    key: NodeKey = (node.kind, node.name, node.path)
    attrs_for_json = dict(node.attrs)
    uri_value = attrs_for_json.pop("uri", None)
    nid = None
    if node.kind == "dependency":
        nid = _dependency_id_by_uri(conn, uri_value)
    if nid is None:
        nid = _node_id(conn, key)
    if nid is not None:
        conn.execute(
            "UPDATE nodes SET name=?, path=?, line=?, attrs_json=?, uri=? WHERE id=?",
            (node.name, node.path, node.line, _serialize(attrs_for_json), uri_value, nid),
        )
        return nid
    return _insert_node(conn, key, node.line, _serialize(attrs_for_json), uri_value)


def _ensure_node(conn: sqlite3.Connection, key: NodeKey) -> int:
    nid = _node_id(conn, key)
    if nid is not None:
        return nid
    return _insert_node(conn, key, None, None, None)


def _symbol_placeholder_key(edge: GraphEdge, attrs: dict[str, Any]) -> NodeKey:
    """Return the persisted dst key for unresolved code-symbol edge targets.

    Parser projections intentionally emit call/export destinations without a
    path because the concrete definition may be external or ambiguous. Store
    those materialized nodes under an explicit kind so DB readers can tell they
    are unresolved references, while keeping the intended symbol kind on the
    edge for the later resolver sweep.
    """
    dst_kind, dst_name, dst_path = edge.dst
    if dst_path is None and dst_kind in _SYMBOL_PLACEHOLDER_KINDS and edge.kind in _SYMBOL_PLACEHOLDER_EDGE_KINDS:
        attrs.setdefault("symbol_kind", dst_kind)
        return (_UNRESOLVED_SYMBOL_KIND, dst_name, None)
    return edge.dst


def _upsert_edge(conn: sqlite3.Connection, edge: GraphEdge) -> None:
    src_id = _ensure_node(conn, edge.src)
    attrs = dict(edge.attrs)
    dst_key = _symbol_placeholder_key(edge, attrs)
    dst_id = _ensure_node(conn, dst_key)
    if dst_key[2] is None:
        attrs.setdefault("resolution", "unresolved")
    attrs_json = _serialize(attrs)
    conn.execute(
        "INSERT INTO edges(src, dst, kind, attrs_json) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(src, dst, kind) DO UPDATE SET attrs_json=excluded.attrs_json",
        (src_id, dst_id, edge.kind, attrs_json),
    )


def upsert_records(conn: sqlite3.Connection, records: GraphRecords) -> None:
    """Upsert nodes and edges from a parser GraphRecords."""
    for node in records.nodes:
        _upsert_node(conn, node)
    for edge in records.edges:
        _upsert_edge(conn, edge)

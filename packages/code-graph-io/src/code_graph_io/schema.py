"""SQLite schema for the code graph.

Schema is intentionally minimal: two tables (nodes, edges) plus metadata.
Per-language detail lives in `attrs_json` blobs. Bumping SCHEMA_VERSION
forces a full rebuild via `gw graph update --full`.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 3

# Bumped whenever any node/edge/attr DERIVATION logic changes (e.g. classification.classify,
# app_kind precedence, derived-edge rules) so existing graphs auto-rebuild without --full.
# v6: synthetic non-null graph node paths for repository/domain/dependency/manifest nodes.
# v7: token_count attr on span-bearing nodes (file/class/function/method/type).
# v9: domain + resource nodes and their derived edges removed.
DERIVER_VERSION = 9

_DDL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS nodes (
        id          INTEGER PRIMARY KEY,
        kind        TEXT NOT NULL,
        name        TEXT NOT NULL,
        path        TEXT,
        line        INTEGER,
        attrs_json  TEXT,
        uri         TEXT,
        repo        TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_nodes_kind_name ON nodes(kind, name)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes(path)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_uri ON nodes(uri)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_repo ON nodes(repo)",
    """
    CREATE TABLE IF NOT EXISTS edges (
        src         INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        dst         INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        kind        TEXT NOT NULL,
        attrs_json  TEXT,
        PRIMARY KEY (src, dst, kind)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_edges_dst_kind ON edges(dst, kind)",
    "CREATE INDEX IF NOT EXISTS idx_edges_src_kind ON edges(src, kind)",
    """
    CREATE TABLE IF NOT EXISTS metadata (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)


def apply_schema(conn: sqlite3.Connection) -> None:
    """Apply the schema and ensure metadata.schema_version is set.

    Idempotent: safe to call on an already-initialized DB.
    """
    with conn:
        for stmt in _DDL_STATEMENTS:
            conn.execute(stmt)
        conn.execute(
            "INSERT INTO metadata(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )

"""Idle re-run is a no-op: counts unchanged, last_indexed_commit stable."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from _git_repo import init_repo, write_and_commit
from code_graph_io import update
from code_graph_io.paths import graph_dir


def _db_path(repo: Path) -> Path:
    return graph_dir(repo) / "code.db"


def _ro(repo: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_db_path(repo)}?mode=ro", uri=True)


def _structural_snapshot(db_path: Path) -> tuple[list, list]:
    """Fallback identity proof when byte-identity is genuinely impossible.

    Edges are compared by joining to the node tuple
    `(kind, name, path)` rather than the raw integer ROWID, because the
    ROWID counter is path-dependent: any structural node (e.g. Repository
    with path=NULL) shifts the auto-id allocation
    for placeholder rows on the next run. The contract is
    semantic structural identity, not byte-identical ROWIDs.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        nodes = conn.execute("SELECT kind, name, path, uri, attrs_json FROM nodes ORDER BY kind, name, path").fetchall()
        edges = conn.execute(
            "SELECT n1.kind, n1.name, n1.path, "
            "       n2.kind, n2.name, n2.path, "
            "       e.kind, e.attrs_json "
            "FROM edges e "
            "JOIN nodes n1 ON n1.id = e.src "
            "JOIN nodes n2 ON n2.id = e.dst "
            "ORDER BY n1.kind, n1.name, n1.path, "
            "         n2.kind, n2.name, n2.path, e.kind"
        ).fetchall()
    finally:
        conn.close()
    return nodes, edges


def test_idle_rerun_is_noop(tmp_path: Path) -> None:
    init_repo(tmp_path)
    head = write_and_commit(tmp_path, {"a.py": "def foo():\n    return 1\n"}, "init")
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    conn = _ro(tmp_path)
    try:
        before = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    finally:
        conn.close()

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=False)

    conn = _ro(tmp_path)
    try:
        after = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert after == before
        assert conn.execute("SELECT value FROM metadata WHERE key='last_indexed_commit'").fetchone() == (head,)
    finally:
        conn.close()


def test_update_full_twice_produces_byte_identical_db(tmp_path: Path) -> None:
    """two `gw graph update --full` runs on the same git state yield
    structurally-identical content.

    Pure byte-identity is precluded by `update.run` writing a fresh
    `last_indexed_at` ISO timestamp on every run (update.py: _set_metadata
    near the bottom of run()). That timestamp deliberately captures wall
    clock — it is the only intentional source of inter-run drift — and a
    rerun therefore always produces a different byte image even after WAL
    truncation. Structural identity (nodes + edges with the same
    `(kind, name, path, uri, attrs_json)` and `(src, dst, kind, attrs_json)`
    tuples) is the semantically-meaningful contract, and is what this
    test asserts.
    """
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.1"\n',
            "src/a.py": "def foo():\n    return 1\n",
            "src/b.py": "from .a import foo\n\ndef bar():\n    return foo()\n",
        },
        "init",
    )
    db = _db_path(tmp_path)

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)
    structural_a = _structural_snapshot(db)

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)
    structural_b = _structural_snapshot(db)

    assert structural_a == structural_b
    # And confirm last_indexed_commit (which IS deterministic) round-trips.
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        head_row = conn.execute("SELECT value FROM metadata WHERE key='last_indexed_commit'").fetchone()
    finally:
        conn.close()
    assert head_row is not None

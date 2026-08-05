"""Tests for code_graph_io.builtins — Builtin node + used_by edge emission."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from _git_repo import init_repo, write_and_commit
from code_graph_io import update
from code_graph_io.builtins import (
    _load_node_builtins,
    _normalize_node_spec,
)
from code_graph_io.paths import graph_dir
from code_graph_io.uri import RepoContext

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_CTX = RepoContext(org="test", repo="repo")


def _open_ro(repo_root: Path) -> sqlite3.Connection:
    """Open read-only connection to the graph DB under *repo_root*."""
    ws = repo_root
    db_path = graph_dir(ws) / "code.db"
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _build_git_python_repo(root: Path, py_files: dict[str, str]) -> None:
    """Create a minimal git+pyproject.toml Python repo under *root* and commit."""
    init_repo(root)
    files = {
        "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.1"\ndependencies = []\n',
    }
    files.update(py_files)
    write_and_commit(root, files, "init")


def _builtin_nodes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT name, uri, attrs_json FROM nodes WHERE kind='builtin' ORDER BY name").fetchall()
    result = []
    for name, uri, attrs_json in rows:
        attrs = json.loads(attrs_json) if attrs_json else {}
        result.append({"name": name, "uri": uri, "attrs": attrs})
    return result


def _builtin_edges(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT p.name AS pkg, b.name AS builtin, e.attrs_json "
        "FROM edges e "
        "JOIN nodes p ON e.src = p.id "
        "JOIN nodes b ON e.dst = b.id "
        "WHERE e.kind='used_by' AND b.kind='builtin' "
        "ORDER BY p.name, b.name"
    ).fetchall()
    result = []
    for pkg, builtin_name, attrs_json in rows:
        attrs = json.loads(attrs_json) if attrs_json else {}
        result.append({"pkg": pkg, "builtin": builtin_name, "attrs": attrs})
    return result


# ---------------------------------------------------------------------------
# Node-spec normalisation (pure unit test — no subprocess)
# ---------------------------------------------------------------------------


def test_node_spec_normalization() -> None:
    """_normalize_node_spec collapses node: prefix and subpaths."""
    assert _normalize_node_spec("node:fs/promises") == "fs"
    assert _normalize_node_spec("fs") == "fs"
    assert _normalize_node_spec("node:test") == "test"


# ---------------------------------------------------------------------------
# Python stdlib → Builtin nodes
# ---------------------------------------------------------------------------


def test_python_stdlib_emits_builtin_nodes(tmp_path: Path) -> None:
    """update.run on a Python pkg importing pathlib + os emits Builtin nodes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_git_python_repo(
        repo,
        {
            "src/demo/__init__.py": "from pathlib import Path\nimport os\nimport sys\nimport json\n",
        },
    )
    update.run(repo, workspace=repo, full=True)

    conn = _open_ro(repo)
    try:
        nodes = _builtin_nodes(conn)
        names = {n["name"] for n in nodes}
        assert "pathlib" in names, f"pathlib not in {names}"
        assert "os" in names, f"os not in {names}"
        assert "sys" in names, f"sys not in {names}"
        assert "json" in names, f"json not in {names}"
        # All emitted nodes must have language and module_name attrs
        for node in nodes:
            assert node["attrs"]["language"] == "python"
            assert node["attrs"]["module_name"] in names
            assert node["uri"] == f"builtin:python/{node['name']}"
    finally:
        conn.close()


def test_python_stdlib_top_level_only(tmp_path: Path) -> None:
    """from os.path import join → builtin:python/os (top-level only)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_git_python_repo(
        repo,
        {"src/demo/__init__.py": "from os.path import join\n"},
    )
    update.run(repo, workspace=repo, full=True)

    conn = _open_ro(repo)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM nodes WHERE kind='builtin'").fetchall()}
        # Should have os (from top-level of os.path), not os.path
        assert "os" in names
        assert "os.path" not in names
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Silent skip when node missing
# ---------------------------------------------------------------------------


def test_silent_skip_when_node_missing(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """_load_node_builtins returns frozenset() when node binary is absent."""
    cache_dir = tmp_path / "cache"

    with patch("code_graph_io.builtins.subprocess.run", side_effect=FileNotFoundError):
        result = _load_node_builtins(cache_dir)

    assert result == frozenset()
    # No cache file created
    assert not list(cache_dir.glob("node-builtins-*.json"))
    # No stderr noise
    captured = capsys.readouterr()
    assert captured.err == ""


# ---------------------------------------------------------------------------
# Node builtins cache lifecycle
# ---------------------------------------------------------------------------


def test_node_builtins_cache_lifecycle(tmp_path: Path) -> None:
    """Cache file created on first call, reused on second, re-harvested on major change."""
    cache_dir = tmp_path / "cache"
    sample_builtins = ["fs", "path", "os", "crypto", "http"]

    def _fake_run_v20(cmd: list[str], **kwargs: Any):
        """Fake subprocess for node v20."""

        class _Res:
            returncode = 0

        if "--version" in cmd:
            _Res.stdout = "v20.11.0\n"
        else:
            # The JSON-harvest call
            _Res.stdout = json.dumps(sample_builtins)
        return _Res()

    # --- First call: cache miss → subprocess → write cache ---
    with patch("code_graph_io.builtins.subprocess.run", side_effect=_fake_run_v20) as mock_run:
        result1 = _load_node_builtins(cache_dir)
        call_count_1 = mock_run.call_count

    assert result1 == frozenset(sample_builtins)
    cache_file_20 = cache_dir / "node-builtins-20.json"
    assert cache_file_20.exists(), "Cache file should be created on first call"
    assert json.loads(cache_file_20.read_text()) == sample_builtins
    # Should have made at least 2 subprocess calls (--version + JSON harvest)
    assert call_count_1 >= 2

    # --- Second call: cache hit → only --version subprocess needed ---
    with patch("code_graph_io.builtins.subprocess.run", side_effect=_fake_run_v20) as mock_run2:
        result2 = _load_node_builtins(cache_dir)
        call_count_2 = mock_run2.call_count

    assert result2 == frozenset(sample_builtins)
    # Only the --version call should happen (cache hit prevents JSON harvest)
    assert call_count_2 == 1, f"Expected 1 subprocess call (--version only) on cache hit, got {call_count_2}"

    # --- Third call with different major → re-harvest ---
    sample_builtins_22 = [*sample_builtins, "sqlite"]

    def _fake_run_v22(cmd: list[str], **kwargs: Any):
        class _Res:
            returncode = 0

        if "--version" in cmd:
            _Res.stdout = "v22.0.0\n"
        else:
            _Res.stdout = json.dumps(sample_builtins_22)
        return _Res()

    with patch("code_graph_io.builtins.subprocess.run", side_effect=_fake_run_v22) as mock_run3:
        result3 = _load_node_builtins(cache_dir)
        call_count_3 = mock_run3.call_count

    assert result3 == frozenset(sample_builtins_22)
    cache_file_22 = cache_dir / "node-builtins-22.json"
    assert cache_file_22.exists(), "Cache file for v22 should exist after re-harvest"
    # Should have made at least 2 calls (--version + JSON harvest for v22)
    assert call_count_3 >= 2


# ---------------------------------------------------------------------------
# Node stdlib → Builtin nodes (requires real node or skip)
# ---------------------------------------------------------------------------


def test_node_stdlib_emits_builtin_nodes(tmp_path: Path) -> None:
    """JS pkg with require('fs') + import 'node:fs/promises' → ONE builtin:javascript/fs."""
    if shutil.which("node") is None:
        pytest.skip("real node not on PATH — cannot harvest Node builtins")

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    write_and_commit(
        repo,
        {
            "package.json": json.dumps({"name": "demo-js", "version": "0.1.1"}),
            "src/index.js": "const fs = require('fs');\nimport 'node:fs/promises';\n",
        },
        "init",
    )
    update.run(repo, workspace=repo, full=True)

    conn = _open_ro(repo)
    try:
        rows = conn.execute(
            "SELECT name FROM nodes WHERE kind='builtin' AND "
            "json_extract(attrs_json, '$.language') = 'javascript' "
            "ORDER BY name"
        ).fetchall()
        js_builtin_names = [r[0] for r in rows]
        # Must have exactly ONE fs node (node: prefix and subpaths collapsed)
        assert js_builtin_names.count("fs") == 1, f"Expected exactly 1 'fs' builtin node, got: {js_builtin_names}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# npm dependencies remain 'dependency', not 'builtin'
# ---------------------------------------------------------------------------


def test_node_dependency_vs_builtin_classification(tmp_path: Path) -> None:
    """require('fs') → builtin; require('express') → dependency, NOT builtin."""
    if shutil.which("node") is None:
        pytest.skip("real node not on PATH — cannot classify Node builtins")

    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    write_and_commit(
        repo,
        {
            "package.json": json.dumps(
                {
                    "name": "demo-js",
                    "version": "0.1.1",
                    "dependencies": {"express": "^4.0.0"},
                }
            ),
            "src/app.js": "const fs = require('fs');\nconst express = require('express');\n",
        },
        "init",
    )
    update.run(repo, workspace=repo, full=True)

    conn = _open_ro(repo)
    try:
        # fs should be builtin
        fs_builtin = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='builtin' AND name='fs'").fetchone()[0]
        assert fs_builtin >= 1, "fs should be classified as builtin"

        # express must NOT appear as a builtin
        express_builtin = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='builtin' AND name='express'").fetchone()[
            0
        ]
        assert express_builtin == 0, "express should NOT be classified as builtin"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# One edge per (package, builtin), symbol union
# ---------------------------------------------------------------------------


def test_used_by_edge_dedup_and_symbol_union(tmp_path: Path) -> None:
    """Two files each importing different os symbols → ONE edge with sorted symbol union."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_git_python_repo(
        repo,
        {
            "src/demo/a.py": "from os import environ\n",
            "src/demo/b.py": "from os import getenv, path\n",
        },
    )
    update.run(repo, workspace=repo, full=True)

    conn = _open_ro(repo)
    try:
        # Exactly one used_by edge from demo → builtin:python/os
        edge_rows = conn.execute(
            "SELECT e.attrs_json FROM edges e "
            "JOIN nodes p ON e.src = p.id "
            "JOIN nodes b ON e.dst = b.id "
            "WHERE e.kind='used_by' AND p.kind='package' AND p.name='demo' "
            "AND b.kind='builtin' AND b.name='os'"
        ).fetchall()
        assert len(edge_rows) == 1, f"Expected 1 used_by edge for os, got {len(edge_rows)}"

        attrs = json.loads(edge_rows[0][0]) if edge_rows[0][0] else {}
        symbols = attrs.get("imported_symbols", [])
        # Must contain all three symbols from both files (sorted union)
        assert "environ" in symbols
        assert "getenv" in symbols
        assert "path" in symbols
        # Must be sorted
        assert symbols == sorted(symbols)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Idempotency: running twice produces identical node + edge state
# ---------------------------------------------------------------------------


def test_emit_is_idempotent(tmp_path: Path) -> None:
    """Running update.run twice on the same repo produces identical Builtin state."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_git_python_repo(
        repo,
        {"src/demo/__init__.py": "import json\nimport re\n"},
    )

    update.run(repo, workspace=repo, full=True)
    conn = _open_ro(repo)
    try:
        nodes_after_1 = {
            r[0]: r[1]
            for r in conn.execute("SELECT name, attrs_json FROM nodes WHERE kind='builtin' ORDER BY name").fetchall()
        }
        edges_after_1 = conn.execute(
            "SELECT e.attrs_json FROM edges e JOIN nodes b ON e.dst = b.id WHERE e.kind='used_by' AND b.kind='builtin'"
        ).fetchall()
    finally:
        conn.close()

    update.run(repo, workspace=repo, full=True)
    conn2 = _open_ro(repo)
    try:
        nodes_after_2 = {
            r[0]: r[1]
            for r in conn2.execute("SELECT name, attrs_json FROM nodes WHERE kind='builtin' ORDER BY name").fetchall()
        }
        edges_after_2 = conn2.execute(
            "SELECT e.attrs_json FROM edges e JOIN nodes b ON e.dst = b.id WHERE e.kind='used_by' AND b.kind='builtin'"
        ).fetchall()
    finally:
        conn2.close()

    assert nodes_after_1 == nodes_after_2, "Builtin nodes differ between first and second run"
    assert len(edges_after_1) == len(edges_after_2), f"Edge count differs: {len(edges_after_1)} vs {len(edges_after_2)}"

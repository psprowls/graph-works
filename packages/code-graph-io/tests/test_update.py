"""Regression tests: per-node language stamping after a full graph build."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from _git_repo import init_repo, write_and_commit
from code_graph_io import update
from code_graph_io.paths import graph_dir


def _open_ro(repo: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{graph_dir(repo) / 'code.db'}?mode=ro", uri=True)


@pytest.mark.integration
def test_full_build_stamps_language_on_nodes(tmp_path: Path) -> None:
    """Regression: file and function nodes carry language after a full build.

    A .py file → language='python'; its functions → language='python'.
    A .ts file → language='typescript'.
    """
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pkg/mod.py": "def hello():\n    return 1\n",
            "pkg/util.ts": "export function add(a: number, b: number) { return a + b; }\n",
        },
        "init",
    )

    update.run(tmp_path, workspace=tmp_path, full=True)

    conn = _open_ro(tmp_path)
    try:
        py_file = conn.execute("SELECT attrs_json FROM nodes WHERE kind='file' AND path='pkg/mod.py'").fetchone()
        assert py_file is not None, "pkg/mod.py file node not found"
        assert json.loads(py_file[0])["language"] == "python"

        py_fn = conn.execute("SELECT attrs_json FROM nodes WHERE kind='function' AND path='pkg/mod.py'").fetchone()
        assert py_fn is not None, "function node in pkg/mod.py not found"
        assert json.loads(py_fn[0])["language"] == "python"

        ts_file = conn.execute("SELECT attrs_json FROM nodes WHERE kind='file' AND path='pkg/util.ts'").fetchone()
        assert ts_file is not None, "pkg/util.ts file node not found"
        assert json.loads(ts_file[0])["language"] == "typescript"
    finally:
        conn.close()


@pytest.mark.integration
def test_full_build_package_node_carries_language(tmp_path: Path) -> None:
    """Regression: a pyproject.toml package node carries language='python' after a full build."""
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "mypkg"\nversion = "0.1.0"\n',
            "src/mypkg/__init__.py": "x = 1\n",
        },
        "init",
    )

    update.run(tmp_path, workspace=tmp_path, full=True)

    conn = _open_ro(tmp_path)
    try:
        pkg_row = conn.execute("SELECT kind, attrs_json FROM nodes WHERE name='mypkg'").fetchone()
        assert pkg_row is not None, "mypkg node not found"
        kind, attrs_json = pkg_row
        assert kind == "package"
        assert json.loads(attrs_json)["language"] == "python"
    finally:
        conn.close()

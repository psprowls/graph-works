"""Interrupted update: rollback leaves last_indexed_commit unchanged."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from _git_repo import init_repo, write_and_commit
from code_graph_io import update
from code_graph_io.paths import graph_dir


def _ro(repo: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{graph_dir(repo) / 'code.db'}?mode=ro", uri=True)


def test_interrupted_update_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_repo(tmp_path)
    head1 = write_and_commit(tmp_path, {"a.py": "def foo():\n    return 1\n"}, "init")
    update.run(tmp_path, workspace=tmp_path, full=True)

    head2 = write_and_commit(tmp_path, {"b.py": "def bar():\n    return 2\n"}, "add b")

    real_sweep = update.resolve.sweep

    def boom(_conn: sqlite3.Connection) -> None:
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(update.resolve, "sweep", boom)

    with pytest.raises(RuntimeError):
        update.run(tmp_path, workspace=tmp_path, full=False)

    conn = _ro(tmp_path)
    try:
        last = conn.execute("SELECT value FROM metadata WHERE key='last_indexed_commit'").fetchone()
        assert last == (head1,)
    finally:
        conn.close()

    monkeypatch.setattr(update.resolve, "sweep", real_sweep)
    update.run(tmp_path, workspace=tmp_path, full=False)
    conn = _ro(tmp_path)
    try:
        assert conn.execute("SELECT value FROM metadata WHERE key='last_indexed_commit'").fetchone() == (head2,)
    finally:
        conn.close()

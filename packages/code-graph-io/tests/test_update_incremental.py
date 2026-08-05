"""Incremental update: only changed files re-parsed; D-status files deleted."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from _git_repo import init_repo, remove_and_commit, write_and_commit
from code_graph_io import update
from code_graph_io.paths import graph_dir


def _ro(repo: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{graph_dir(repo) / 'code.db'}?mode=ro", uri=True)


def test_incremental_picks_up_modified_file(tmp_path: Path) -> None:
    init_repo(tmp_path)
    write_and_commit(tmp_path, {"a.py": "def foo():\n    return 1\n"}, "init")
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    head2 = write_and_commit(
        tmp_path,
        {"a.py": "def foo():\n    return 1\n\ndef bar():\n    return 2\n"},
        "add bar",
    )
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=False)

    conn = _ro(tmp_path)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM nodes WHERE kind='function'").fetchall()}
        assert names == {"foo", "bar"}
        assert conn.execute("SELECT value FROM metadata WHERE key='last_indexed_commit'").fetchone() == (head2,)
    finally:
        conn.close()


def test_incremental_deletes_removed_file(tmp_path: Path) -> None:
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {"a.py": "def foo():\n    return 1\n", "b.py": "def bar():\n    return 2\n"},
        "init",
    )
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    remove_and_commit(tmp_path, ["b.py"], "remove b")
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=False)

    conn = _ro(tmp_path)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM nodes WHERE kind='function'").fetchall()}
        assert names == {"foo"}
        files = {row[0] for row in conn.execute("SELECT path FROM nodes WHERE kind='file'").fetchall()}
        assert files == {"a.py"}
    finally:
        conn.close()


def test_incremental_handles_rename(tmp_path: Path) -> None:
    init_repo(tmp_path)
    write_and_commit(tmp_path, {"a.py": "def foo():\n    return 1\n"}, "init")
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    subprocess.run(["git", "mv", "a.py", "b.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "rename"], cwd=tmp_path, check=True)
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=False)

    conn = _ro(tmp_path)
    try:
        files = {row[0] for row in conn.execute("SELECT path FROM nodes WHERE kind='file'").fetchall()}
        assert files == {"b.py"}
        rows = conn.execute("SELECT path FROM nodes WHERE kind='function' AND name='foo'").fetchall()
        assert rows == [("b.py",)]
    finally:
        conn.close()


def test_incremental_scan_leaves_no_external_import_stubs(tmp_path: Path) -> None:
    """End-to-end: `gw scan` == update.run(full=False). An incremental scan must
    not leave NULL-uri kind='file' stubs for stdlib/third-party imports, and a
    first-party submodule import must resolve to the real file node.

    full=False is essential and intentional: it is the actual `gw scan` path, and
    unlike full=True it does NOT run the blanket node cleanup in update.run that
    would delete these stubs regardless of resolve_file_imports. So this exercises
    resolve_file_imports as the sole stub remover. (Pre-fix, the os/json/contextlib
    stubs survived this exact path.) Do not "simplify" this to full=True — that
    would mask the regression.
    """
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "mypkg/pyproject.toml": '[project]\nname = "mypkg"\n',
            "mypkg/src/mypkg/__init__.py": "",
            "mypkg/src/mypkg/app.py": (
                "import os\nimport json\nfrom contextlib import contextmanager\nfrom mypkg.helper import helper_fn\n"
            ),
            "mypkg/src/mypkg/helper.py": "def helper_fn():\n    return 1\n",
        },
        "init",
    )

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=False)

    conn = _ro(tmp_path)
    try:
        # No external/stdlib import stub file nodes survive.
        stubs = conn.execute(
            "SELECT name, path FROM nodes WHERE kind='file' AND uri IS NULL AND path IN ('os', 'json', 'contextlib')"
        ).fetchall()
        assert stubs == [], f"external import stubs survived: {stubs}"

        # src-layout: `from mypkg.helper import helper_fn` resolves via the
        # pyproject-discovered import root to mypkg/src/mypkg/helper.py.
        resolved = conn.execute(
            "SELECT COUNT(*) FROM edges e "
            "JOIN nodes s ON e.src=s.id JOIN nodes d ON e.dst=d.id "
            "WHERE e.kind='imports' "
            "AND s.path='mypkg/src/mypkg/app.py' "
            "AND d.path='mypkg/src/mypkg/helper.py' "
            "AND d.uri IS NOT NULL"
        ).fetchone()[0]
        assert resolved == 1, "first-party import did not resolve to helper.py"
    finally:
        conn.close()


def test_incremental_scan_stub_cleanup_is_idempotent(tmp_path: Path) -> None:
    """A second incremental scan that re-processes both the importer AND its
    first-party target must not resurrect external stubs nor flip the resolved
    first-party edge. Pins the is_file() discriminator that replaced the old
    name!=path idempotency guard (real files transiently have uri IS NULL while
    resolve_file_imports runs)."""
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "mypkg/pyproject.toml": '[project]\nname = "mypkg"\n',
            "mypkg/src/mypkg/__init__.py": "",
            "mypkg/src/mypkg/app.py": ("import os\nfrom mypkg.helper import helper_fn\n"),
            "mypkg/src/mypkg/helper.py": "def helper_fn():\n    return 1\n",
        },
        "init",
    )
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=False)

    # Second incremental scan re-processing BOTH files (so the import target is
    # re-upserted and passes through its transient uri IS NULL window).
    write_and_commit(
        tmp_path,
        {
            "mypkg/src/mypkg/app.py": ("import os\nfrom mypkg.helper import helper_fn\n# touch\n"),
            "mypkg/src/mypkg/helper.py": "def helper_fn():\n    return 2\n",
        },
        "touch both",
    )
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=False)

    conn = _ro(tmp_path)
    try:
        # No external stub resurrected.
        assert (
            conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='file' AND uri IS NULL AND path='os'").fetchone()[0]
            == 0
        )
        # First-party edge still resolved to the real (uri-bearing) helper.py.
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM edges e JOIN nodes d ON e.dst=d.id "
                "WHERE e.kind='imports' AND d.path='mypkg/src/mypkg/helper.py' "
                "AND d.uri IS NOT NULL"
            ).fetchone()[0]
            == 1
        )
        # The real target file was NOT deleted by the stub cleanup.
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE kind='file' AND path='mypkg/src/mypkg/helper.py'"
            ).fetchone()[0]
            == 1
        )
    finally:
        conn.close()

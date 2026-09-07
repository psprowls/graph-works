"""Full update: tiny multi-file git repo → populated DB."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from _git_repo import init_repo, write_and_commit
from code_graph_io import schema, update
from code_graph_io.paths import graph_dir


def _open_ro(repo: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{graph_dir(repo) / 'code.db'}?mode=ro", uri=True)


def test_update_full_populates_db(tmp_path: Path) -> None:
    init_repo(tmp_path)
    head = write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.1"\n',
            "src/a.py": "def foo():\n    return 1\n",
            "src/b.py": "from .a import foo\n\ndef bar():\n    return foo()\n",
        },
        "init",
    )

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    conn = _open_ro(tmp_path)
    try:
        kinds = {row[0] for row in conn.execute("SELECT DISTINCT kind FROM nodes").fetchall()}
        assert {"file", "function", "package"} <= kinds
        names = {row[0] for row in conn.execute("SELECT name FROM nodes WHERE kind='function'").fetchall()}
        assert {"foo", "bar"} <= names
        last = conn.execute("SELECT value FROM metadata WHERE key='last_indexed_commit'").fetchone()
        assert last == (head,)
    finally:
        conn.close()


def test_update_raises_outside_git(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(update.NotInGitRepoError):
        update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)


def test_update_skips_default_skip_dirs(tmp_path: Path) -> None:
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.1"\n',
            "src/a.py": "def keep_me():\n    return 1\n",
            "dist/junk.py": "def skip_me():\n    return 2\n",
        },
        "init",
    )

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    conn = _open_ro(tmp_path)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM nodes WHERE kind='function'").fetchall()}
        assert "keep_me" in names
        assert "skip_me" not in names

        paths = {row[0] for row in conn.execute("SELECT path FROM nodes WHERE kind='file'").fetchall()}
        assert "src/a.py" in paths
        assert "dist/junk.py" not in paths
    finally:
        conn.close()


def _db_path(repo: Path) -> Path:
    return graph_dir(repo) / "code.db"


def test_deriver_version_bump_forces_rebuild(tmp_path: Path) -> None:
    """A deriver_version mismatch forces a full rebuild at unchanged HEAD."""
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.1"\n',
            "src/a.py": "def real_func():\n    return 1\n",
        },
        "init",
    )

    # First full build — stamps deriver_version in metadata.
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    conn = _open_ro(tmp_path)
    try:
        dv = conn.execute("SELECT value FROM metadata WHERE key='deriver_version'").fetchone()
        assert dv == (str(schema.DERIVER_VERSION),)
    finally:
        conn.close()

    # Simulate an older build: stamp deriver_version back to '0' so the next
    # run sees a mismatch.  Also DELETE the function node to prove the full
    # rebuild re-derives and re-inserts it.
    db = _db_path(tmp_path)
    wconn = sqlite3.connect(str(db))
    try:
        wconn.execute("UPDATE metadata SET value='0' WHERE key='deriver_version'")
        wconn.execute("DELETE FROM nodes WHERE kind='function'")
        wconn.commit()
    finally:
        wconn.close()

    # Verify function node is gone before re-run.
    conn = _open_ro(tmp_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='function'").fetchone()[0]
        assert count == 0
    finally:
        conn.close()

    # Re-run with full=False at the *same* HEAD — mismatch must force rebuild.
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=False)

    conn = _open_ro(tmp_path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM nodes WHERE kind='function'").fetchall()}
        # Full rebuild re-derived the function node.
        assert "real_func" in names
        # deriver_version stamp updated.
        dv = conn.execute("SELECT value FROM metadata WHERE key='deriver_version'").fetchone()
        assert dv == (str(schema.DERIVER_VERSION),)
    finally:
        conn.close()


def test_unchanged_deriver_version_still_short_circuits(tmp_path: Path) -> None:
    """When deriver_version is current and HEAD is unchanged, run() short-circuits."""
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.1"\n',
            "src/a.py": "def real_func():\n    return 1\n",
        },
        "init",
    )

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    # Delete function node — if short-circuit fires it stays gone (no rebuild).
    db = _db_path(tmp_path)
    wconn = sqlite3.connect(str(db))
    try:
        wconn.execute("DELETE FROM nodes WHERE kind='function'")
        wconn.commit()
    finally:
        wconn.close()

    # Re-run at same HEAD with matching deriver_version → must short-circuit.
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=False)

    conn = _open_ro(tmp_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='function'").fetchone()[0]
        # Short-circuit returned early — function node not re-derived.
        assert count == 0
    finally:
        conn.close()


def test_run_workspace_honors_member_ignore(tmp_path: Path) -> None:
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.1"\n',
            "src/a.py": "def keep_me():\n    return 1\n",
            "generated/auto.py": "def skip_me():\n    return 2\n",
        },
        "init",
    )

    update.run_workspace([tmp_path], graph_dir=graph_dir(tmp_path), full=True, member_ignore=[("generated/**",)])

    conn = _open_ro(tmp_path)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM nodes WHERE kind='function'").fetchall()}
        assert "keep_me" in names
        assert "skip_me" not in names
        paths = {row[0] for row in conn.execute("SELECT path FROM nodes WHERE kind='file'").fetchall()}
        assert not any(p is not None and p.startswith("generated/") for p in paths)
    finally:
        conn.close()


def test_run_workspace_full_recompute_prunes_newly_ignored_nodes(tmp_path: Path) -> None:
    """Regression: the full-mode tracked-paths recompute must honor `ignore`
    too, not just _process_files — a file present in a prior full build must
    be pruned once a later full build ignores it."""
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.1"\n',
            "src/a.py": "def keep_me():\n    return 1\n",
            "generated/auto.py": "def skip_me():\n    return 2\n",
        },
        "init",
    )

    # First full build: nothing ignored yet — skip_me should exist.
    update.run_workspace([tmp_path], graph_dir=graph_dir(tmp_path), full=True)
    conn = _open_ro(tmp_path)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM nodes WHERE kind='function'").fetchall()}
        assert "skip_me" in names
    finally:
        conn.close()

    # Second full build: generated/** is now ignored — skip_me must be pruned.
    update.run_workspace([tmp_path], graph_dir=graph_dir(tmp_path), full=True, member_ignore=[("generated/**",)])
    conn = _open_ro(tmp_path)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM nodes WHERE kind='function'").fetchall()}
        assert "keep_me" in names
        assert "skip_me" not in names
    finally:
        conn.close()


def test_run_workspace_member_ignore_defaults_to_none_per_member(tmp_path: Path) -> None:
    """Omitting member_ignore must behave identically to today: no member
    gets an IgnoreSpec, only DEFAULT_SKIP_DIRS applies."""
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.1"\n',
            "generated/auto.py": "def keep_me():\n    return 1\n",
        },
        "init",
    )

    update.run_workspace([tmp_path], graph_dir=graph_dir(tmp_path), full=True)

    conn = _open_ro(tmp_path)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM nodes WHERE kind='function'").fetchall()}
        assert "keep_me" in names
    finally:
        conn.close()


def test_repository_dep_edges_survive_a_full_build(tmp_path: Path) -> None:
    """Regression: a virtual root's external dependency edge, re-sourced to
    the Repository node, must survive `--full`. The Repository node doesn't
    exist until `structural_nodes.emit` runs, well after `packages.refresh` —
    and the full-mode cleanup DELETE sits between the two and does not
    exclude kind='repository', so a naive in-`refresh()` write is dropped."""
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "ws"\nversion = "0.0.0"\n'
                "[tool.uv]\npackage = false\n"
                '[dependency-groups]\ndev = ["mypy>=1.0"]\n'
            ),
            "packages/alpha/pyproject.toml": '[project]\nname = "alpha"\nversion = "0.1.0"\n',
        },
        "init",
    )

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    conn = _open_ro(tmp_path)
    try:
        row = conn.execute(
            "SELECT e.attrs_json FROM edges e "
            "JOIN nodes src ON e.src = src.id "
            "JOIN nodes dst ON e.dst = dst.id "
            "WHERE e.kind='used_by' AND src.kind='repository' AND dst.kind='dependency' AND dst.name='mypy'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_token_count_on_function_node(seeded_db) -> None:
    row = seeded_db.execute(
        "SELECT attrs_json FROM nodes WHERE kind='function' AND name='foo' AND path LIKE '%foo.py'"
    ).fetchone()
    assert row is not None, "expected the 'foo' function node"
    attrs = json.loads(row[0]) if row[0] else {}
    assert isinstance(attrs.get("token_count"), int)
    assert attrs["token_count"] > 0


def test_token_count_on_file_node(seeded_db) -> None:
    row = seeded_db.execute("SELECT attrs_json FROM nodes WHERE kind='file' AND path LIKE '%foo.py'").fetchone()
    assert row is not None, "expected the foo.py file node"
    attrs = json.loads(row[0]) if row[0] else {}
    assert isinstance(attrs.get("token_count"), int)
    assert attrs["token_count"] > 0


def test_no_token_count_on_synthetic_package_node(seeded_db) -> None:
    row = seeded_db.execute("SELECT attrs_json FROM nodes WHERE kind='package' LIMIT 1").fetchone()
    assert row is not None, "expected at least one package node"
    attrs = json.loads(row[0]) if row[0] else {}
    assert "token_count" not in attrs


def test_member_ignore_excludes_a_manifest_not_just_its_files(tmp_path: Path) -> None:
    """An ignored subtree must yield no Package node, not just no File nodes.

    Filtering the file walk alone left a fixture/vendored tree's
    `pyproject.toml` producing a Package node — and one entity page per
    fixture package downstream — which is the drift `ignore:` exists to stop.
    """
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "realpkg"\nversion = "0.1.0"\n',
            "src/a.py": "def keep_me():\n    return 1\n",
            "pkg/tests/fixtures/sample/pyproject.toml": '[project]\nname = "fixturepkg"\nversion = "0.1.0"\n',
            "pkg/tests/fixtures/sample/b.py": "def skip_me():\n    return 2\n",
        },
        "init",
    )

    update.run_workspace([tmp_path], graph_dir=graph_dir(tmp_path), full=True, member_ignore=[("**/fixtures/**",)])

    conn = _open_ro(tmp_path)
    try:
        packages = {row[0] for row in conn.execute("SELECT name FROM nodes WHERE kind='package'").fetchall()}
        assert packages == {"realpkg"}
        paths = {row[0] for row in conn.execute("SELECT path FROM nodes WHERE kind='file'").fetchall()}
        assert not any(path.startswith("pkg/tests/fixtures/") for path in paths)
    finally:
        conn.close()


_SYMBOLS_SRC = "class Widget:\n    def spin(self):\n        return 1\n\n\ndef top():\n    return 2\n"


def test_full_build_keeps_symbol_nodes_with_posix_paths(tmp_path: Path) -> None:
    """Symbol nodes survive the stale-node purge, spelled the way the purge spells paths.

    The purge builds `tracked_paths` from git's POSIX-spelled output. A parser
    projection that stringified its `Path` with the OS separator emitted
    `src\\a.py` on Windows, which is `NOT IN tracked_paths`, so every function,
    class and method was deleted on every full build and the graph silently held
    no symbols at all. `file` nodes hid the loss: `structural_nodes.refresh()`
    runs after the purge and re-emits them with POSIX paths.
    """
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.1"\n',
            "src/nested/deep.py": _SYMBOLS_SRC,
        },
        "init",
    )

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    conn = _open_ro(tmp_path)
    try:
        rows = conn.execute(
            "SELECT kind, name, path FROM nodes WHERE kind IN ('function', 'class', 'method')"
        ).fetchall()
        assert rows, "the full build purged every symbol node"
        assert ("function", "top", "src/nested/deep.py") in rows
        paths = {row[2] for row in rows}
        assert paths == {"src/nested/deep.py"}, f"symbol paths not POSIX-spelled: {paths}"
    finally:
        conn.close()

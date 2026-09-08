"""Ignore-pattern config drift: incremental mode must not strand nodes under
a newly-ignored path just because no file under it changed.

`package`/`dependency` nodes already self-heal on every run via
`packages.refresh`'s `_prune_vanished` (it re-derives its keep-set from disk
every call, incremental or full). Every other kind — `test_suite`,
`subpackage`, `entry_point`, `file`, `function` — only gets pruned by the
`full:`-gated cleanup DELETE in `update.py`, which incremental mode never
triggers on its own: its sole input is a git diff, and an `ignore:` edit is
invisible to one.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from _git_repo import init_repo, write_and_commit
from code_graph_io import update
from code_graph_io.paths import graph_dir


def _ro(repo: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{graph_dir(repo) / 'code.db'}?mode=ro", uri=True)


def _seed_repo_with_a_package_worth_ignoring(tmp_path: Path) -> None:
    genpkg_toml = '[project]\nname = "genpkg"\nversion = "0.1.0"\n[project.scripts]\ngen-cli = "genpkg.cli:main"\n'
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "realpkg"\nversion = "0.1.0"\n',
            "src/realpkg/__init__.py": "",
            "src/realpkg/keep.py": "def keep_me():\n    return 1\n",
            "pkg/pyproject.toml": genpkg_toml,
            "pkg/src/genpkg/__init__.py": "",
            "pkg/src/genpkg/cli.py": "def main():\n    return 0\n",
            "pkg/src/genpkg/sub/__init__.py": "",
            "pkg/src/genpkg/sub/mod.py": "def helper():\n    return 1\n",
            "pkg/tests/test_thing.py": "def test_thing():\n    assert True\n",
        },
        "init",
    )


def test_broadening_ignore_incrementally_retracts_every_stranded_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression for work/bug-incremental-ignore-retraction.

    Broadening `ignore:` between two *incremental* runs (no file changes in
    between) must retract every kind under the newly-ignored path — not just
    `package`, which was already handled by a different mechanism.
    """
    init_repo(tmp_path)
    _seed_repo_with_a_package_worth_ignoring(tmp_path)

    # First run: nothing ignored yet. Seeds every kind under pkg/.
    update.run_workspace([tmp_path], graph_dir=graph_dir(tmp_path), full=True)
    conn = _ro(tmp_path)
    try:
        kinds = {row[0] for row in conn.execute("SELECT DISTINCT kind FROM nodes WHERE path LIKE 'pkg/%'").fetchall()}
        assert {"file", "function", "subpackage", "entry_point"} <= kinds
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='package' AND name='genpkg'").fetchone()[0] == 1
    finally:
        conn.close()

    # Second run: broaden ignore to exclude pkg/ entirely. No file changed —
    # full=False, and the ignore set is the ONLY input that moved.
    capsys.readouterr()
    update.run_workspace([tmp_path], graph_dir=graph_dir(tmp_path), full=False, member_ignore=[("pkg/**",)])
    err = capsys.readouterr().err
    assert "ignore: patterns changed" in err
    assert "forcing full rebuild" in err

    conn = _ro(tmp_path)
    try:
        stranded = conn.execute("SELECT kind, COUNT(*) FROM nodes WHERE path LIKE 'pkg/%' GROUP BY kind").fetchall()
        assert stranded == [], f"nodes stranded under pkg/: {stranded}"
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='package' AND name='genpkg'").fetchone()[0] == 0
        names = {row[0] for row in conn.execute("SELECT name FROM nodes WHERE kind='function'").fetchall()}
        assert "keep_me" in names
    finally:
        conn.close()


def test_ignored_manifest_yields_no_dependency_node(tmp_path: Path) -> None:
    """Regression for work/bug-scanner-writes-a-dependency-page-for (absorbed
    scope): a distributable manifest under an ignored path must not mint a
    `dependency` node at all — not merely skip its rendered page. This is the
    gap the design's Phase 1d identified: `webutil.md` survived on disk not
    because of an ignore-glob defect (the node was already correctly absent
    from the graph), but because of the prose guard. No prior test asserted
    the graph side of this, only a docstring mention of `dependency` nodes in
    `test_broadening_ignore_incrementally_retracts_every_stranded_kind`."""
    init_repo(tmp_path)
    _seed_repo_with_a_package_worth_ignoring(tmp_path)

    update.run_workspace(
        [tmp_path],
        graph_dir=graph_dir(tmp_path),
        full=True,
        member_ignore=[("pkg/**",)],
    )

    conn = _ro(tmp_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='dependency' AND name='genpkg'").fetchone()[0] == 0
        # The un-ignored sibling manifest still gets its own dependency node —
        # proves the absence above is the ignore glob at work, not a
        # collateral break of dependency reconciliation itself.
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='dependency' AND name='realpkg'").fetchone()[0] == 1
    finally:
        conn.close()


def test_unchanged_ignore_patterns_do_not_force_a_rebuild(tmp_path: Path) -> None:
    """The fingerprint must be stable across runs with identical patterns: an
    incremental run whose ignore set did NOT move must still short-circuit at
    unchanged HEAD, exactly like `test_unchanged_deriver_version_still_short_circuits`
    (`test_update_full.py`) does for the sibling `deriver_version` mechanism.
    """
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.0"\n',
            "src/a.py": "def real_func():\n    return 1\n",
        },
        "init",
    )
    update.run_workspace([tmp_path], graph_dir=graph_dir(tmp_path), full=True, member_ignore=[("generated/**",)])

    db = graph_dir(tmp_path) / "code.db"
    wconn = sqlite3.connect(str(db))
    try:
        wconn.execute("DELETE FROM nodes WHERE kind='function'")
        wconn.commit()
    finally:
        wconn.close()

    # Same HEAD, same ignore patterns, full=False → must short-circuit.
    update.run_workspace([tmp_path], graph_dir=graph_dir(tmp_path), full=False, member_ignore=[("generated/**",)])

    conn = _ro(tmp_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='function'").fetchone()[0]
        # Short-circuit returned early — function node not re-derived (it
        # would be, if fingerprint comparison spuriously forced full=True).
        assert count == 0
    finally:
        conn.close()

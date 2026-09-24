"""Regression: a requirements-only FastAPI backend beside a Vite frontend (Argus layout).

Before the fix, the backend surfaced only as File nodes: no Package, App,
TestSuite or pypi Dependency nodes, and its tests stayed on the Repository.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from _argus_layout import ARGUS_FILES, ARGUS_IGNORE
from _git_repo import init_repo, write_and_commit
from code_graph_io import update
from code_graph_io.paths import graph_dir


def _scan(repo: Path, *, full: bool) -> None:
    update.run_workspace([repo], graph_dir=graph_dir(repo), full=full, member_ignore=[ARGUS_IGNORE])


def _ro(repo: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{graph_dir(repo) / 'code.db'}?mode=ro", uri=True)


def _entity_identities(repo: Path) -> set[tuple[str, str, str, str | None]]:
    conn = _ro(repo)
    try:
        rows = conn.execute(
            "SELECT kind, name, path, uri FROM nodes "
            "WHERE kind IN ('package', 'app', 'test_suite', 'dependency', 'repository')"
        ).fetchall()
    finally:
        conn.close()
    return {(r[0], r[1], r[2], r[3]) for r in rows}


def test_argus_layout_backend_entities_appear_and_are_stable(tmp_path: Path) -> None:
    init_repo(tmp_path)
    write_and_commit(tmp_path, ARGUS_FILES, "argus layout")

    _scan(tmp_path, full=True)
    first = _entity_identities(tmp_path)

    conn = _ro(tmp_path)
    try:
        pkgs = {r[0]: r[1] for r in conn.execute("SELECT path, name FROM nodes WHERE kind='package'").fetchall()}
        assert pkgs == {"backend": "backend", "frontend": "argus-frontend"}, "no root duplicate, no ignored scratch"

        apps = {
            r[0]: json.loads(r[1])["app_kind"]
            for r in conn.execute("SELECT path, attrs_json FROM nodes WHERE kind='app'").fetchall()
        }
        assert apps == {"backend": "server", "frontend": "spa"}

        suites = {r[0] for r in conn.execute("SELECT path FROM nodes WHERE kind='test_suite'").fetchall()}
        assert {"backend/tests", "frontend/tests"} <= suites

        # The backend test file's single physical parent is the backend suite, not the Repository.
        parents = conn.execute(
            "SELECT p.kind, p.path FROM edges e JOIN nodes p ON e.src=p.id JOIN nodes f ON e.dst=f.id "
            "WHERE e.kind='physically_contains' AND f.kind='file' AND f.path='backend/tests/test_health.py'"
        ).fetchall()
        assert parents == [("test_suite", "backend/tests")]

        # Package contains the backend source file.
        contains = conn.execute(
            "SELECT 1 FROM edges e JOIN nodes p ON e.src=p.id JOIN nodes f ON e.dst=f.id "
            "WHERE e.kind='contains' AND p.kind='package' AND p.path='backend' AND f.path='backend/app.py'"
        ).fetchone()
        assert contains is not None

        dep_uris = {r[0] for r in conn.execute("SELECT uri FROM nodes WHERE kind='dependency'").fetchall()}
        prefix = (
            "dependency:"
            + conn.execute("SELECT uri FROM nodes WHERE kind='repository'").fetchone()[0].removeprefix("repo:")
            + "/"
        )
        assert {
            prefix + "pypi/fastapi",
            prefix + "pypi/uvicorn",
            prefix + "pypi/pydantic",
            prefix + "pypi/numpy",
            prefix + "pypi/pytest",
            prefix + "npm/react",
        } <= dep_uris
        assert prefix + "pypi/requests" not in dep_uris, "ignored backend/scratch must not contribute deps"

        # Exclusion still applies to files.
        assert conn.execute("SELECT 1 FROM nodes WHERE path LIKE 'backend/scratch/%'").fetchone() is None
    finally:
        conn.close()

    # Repeated scans keep identities stable (full and incremental).
    _scan(tmp_path, full=True)
    assert _entity_identities(tmp_path) == first
    _scan(tmp_path, full=False)
    assert _entity_identities(tmp_path) == first

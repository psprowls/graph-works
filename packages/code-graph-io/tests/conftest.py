"""Add tests/ to sys.path so _git_repo helpers are importable without a package prefix."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from code_graph_io.schema import apply_schema


@pytest.fixture(scope="session", autouse=True)
def _isolate_from_ambient_workspace():
    """Strip `GRAPH_WIKI_WORKSPACE` for the whole code-graph-io test session.

    code-graph-io tests build their own temp workspaces; no test reads the ambient
    env var. If a developer has it pointed at a real workspace, `config.resolve`
    would bind the seeded_db fixture's writes there — clobbering that workspace's
    `.agent-workspace.yaml` and code.db (the D7 manifest write made this destructive).
    Removing it categorically prevents any code-graph-io test from touching it.
    """
    mp = pytest.MonkeyPatch()
    mp.delenv("GRAPH_WIKI_WORKSPACE", raising=False)
    yield
    mp.undo()


@pytest.fixture(scope="session")
def seeded_workspace(tmp_path_factory) -> Path:
    """Workspace dir whose `.agent-workspace/code.db` is built by `update.run(..., full=True)`.

    Session-scoped: builds the sample_monorepo graph once and returns the
    workspace `Path` (not a connection). `seeded_db` opens a read-only conn over
    the same workspace; the handle-API tests pass this path straight to
    `open_reader`/`open_writer`.

    The workspace is a temp sibling of the seeded repo, constructed here and
    passed explicitly to `update.run`, so the fixture never resolves against
    the ambient `GRAPH_WIKI_WORKSPACE` (see `_isolate_from_ambient_workspace`).
    """
    # Lazy import to avoid forcing the import at conftest collection time
    # for tests that do not need the seeded DB.
    from code_graph_io import update

    fixture_src = Path(__file__).parent / "fixtures" / "sample_monorepo"
    repo_root = tmp_path_factory.mktemp("queries_seed") / "repo"
    shutil.copytree(fixture_src, repo_root)

    # `gw graph update` requires the workspace to be a git repository.
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_root,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo_root, check=True)
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seeded_db init"], cwd=repo_root, check=True)

    ws = repo_root.parent / "agent-workspace"
    ws.mkdir(parents=True, exist_ok=True)

    update.run(repo_root, workspace=ws, full=True)
    return ws


@pytest.fixture(scope="session")
def seeded_db(seeded_workspace):
    """Read-only conn over sample_monorepo after `update.run(..., full=True)`.

    Session-scoped: one update run per test session; all callers
    share the resulting `mode=ro` connection. Safe because every
    query helper opens read-only and issues no INSERT/UPDATE/DELETE.
    """
    from code_graph_io.paths import graph_dir

    db_path = graph_dir(seeded_workspace) / "code.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def empty_db():
    """Empty in-memory DB with the schema applied; function-scoped."""
    conn = sqlite3.connect(":memory:")
    apply_schema(conn)
    try:
        yield conn
    finally:
        conn.close()

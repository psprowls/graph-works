"""The fixture graph both graph-surface test modules read.

Seeded through `code_graph_io.testing.raw_conn` — the sanctioned test-only
opener — rather than a live scan, so no test here touches git, the network,
or this repo's own working tree.

Node shapes are read from the real emitters and the queries that consume
them: `describe_builtin` matches `name = module_name AND path = language`,
`describe_dependency` matches `name` plus `attrs.ecosystem`, and
`describe_repository`/`describe_dependency`/`describe_agent_plugin` read the
`uri` *column*, not an attr. Getting one of those wrong yields a silent None,
not an error, which is why they are named here.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from code_graph_io.testing import raw_conn

# `tests/work/_transaction_helpers.py` is a plain (non-package) module shared
# by `tests/work/test_transactions.py` and `tests/workspace/test_windows_anchor.py`.
# Neither `tests/` nor its subdirectories carry `__init__.py`, so pytest's
# default "prepend" import mode only ever puts a test module's OWN directory
# on `sys.path` -- it happens to also work when `tests/work` is collected
# before `tests/workspace` (alphabetical order), but that is an accident of
# collection order, not a guarantee, and it breaks outright when
# `test_windows_anchor.py` is run on its own. This conftest is collected
# before any test module in the run, regardless of target, so inserting the
# helper's directory here is the one place that is order-independent.
sys.path.insert(0, str(Path(__file__).parent / "work"))

_ORG = "acme"
_REPO = "demo"

_NODES: tuple[tuple[int, str, str, str | None, int | None, dict[str, object] | None, str | None], ...] = (
    # id, kind, name, path, line, attrs, uri
    (
        1,
        "repository",
        _REPO,
        "",
        None,
        {"owner": _ORG, "url": "https://example.com/acme/demo", "default_branch": "main"},
        f"repo:{_ORG}/{_REPO}",
    ),
    (
        2,
        "package",
        "widgets",
        "packages/widgets/pyproject.toml",
        None,
        {"language": "python", "version": "1.2.3"},
        f"pkg:{_ORG}/{_REPO}/widgets",
    ),
    (3, "file", "a.py", "packages/widgets/src/a.py", None, {"language": "python"}, None),
    (4, "file", "b.py", "packages/widgets/src/b.py", None, {"language": "python"}, None),
    (5, "function", "alpha", "packages/widgets/src/a.py", 10, {}, None),
    (6, "function", "beta", "packages/widgets/src/a.py", 20, {}, None),
    (
        7,
        "entry_point",
        "demo-cli",
        "packages/widgets/src/a.py",
        None,
        {"entry_kind": "console_script", "callable": "a:main", "source": "pyproject"},
        f"entry_point:{_ORG}/{_REPO}/widgets/demo-cli",
    ),
    (
        8,
        "test_suite",
        "tests",
        "tests",
        None,
        {"suite_kind": "unit", "path": "tests", "owner_kind": "repository"},
        f"test_suite:{_ORG}/{_REPO}/tests",
    ),
    (
        9,
        "app",
        "console",
        "apps/console/package.json",
        None,
        {"language": "typescript", "version": "1.0.0", "app_kind": "cli", "app_signals": []},
        f"app:{_ORG}/{_REPO}/console",
    ),
    (
        10,
        "dependency",
        "requests",
        "dependency:pypi/requests",
        None,
        {"ecosystem": "pypi", "versions_in_use": ["2.31.0"]},
        "dependency:pypi/requests",
    ),
    # `describe_builtin` keys on (name=module_name, path=language). The path
    # column carrying a language is not a typo.
    (
        11,
        "builtin",
        "json",
        "python",
        None,
        {"language": "python", "module_name": "json"},
        "builtin:python/json",
    ),
    (
        12,
        "agent_plugin",
        "demo-plugin",
        ".claude-plugin",
        None,
        {
            "ecosystem": "claude-code",
            "name": "demo-plugin",
            "version": "1.0.0",
            "description": "demo plugin",
            "components": {
                "commands": [],
                "agents": [],
                "skills": [],
                "scripts": [],
                "hooks": [],
                "mcp_servers": [],
            },
        },
        f"agent_plugin:{_ORG}/{_REPO}/demo-plugin",
    ),
    # Two entry points sharing one name, one on the package and one on the app —
    # the AMBIGUOUS(7) path in §4.5's table has no other way to fire.
    (
        13,
        "entry_point",
        "shared",
        "packages/widgets/src/a.py",
        None,
        {"entry_kind": "console_script", "callable": "a:shared", "source": "pyproject"},
        f"entry_point:{_ORG}/{_REPO}/widgets/shared",
    ),
    (
        14,
        "entry_point",
        "shared",
        "apps/console/index.ts",
        None,
        {"entry_kind": "bin", "callable": "index.ts", "source": "package.json"},
        f"entry_point:{_ORG}/{_REPO}/console/shared",
    ),
    # A same-named function in two different packages/apps — the only
    # cross-package code-symbol collision in the fixture, added specifically
    # to exercise `--in-package` actually narrowing (as opposed to `shared`,
    # which collides by kind=entry_point, not by code symbol).
    (15, "function", "gamma", "packages/widgets/src/b.py", 5, {}, None),
    (16, "file", "index.ts", "apps/console/src/index.ts", None, {"language": "typescript"}, None),
    (17, "function", "gamma", "apps/console/src/index.ts", 5, {}, None),
)

_EDGES: tuple[tuple[int, int, str], ...] = (
    (1, 2, "contains"),
    (1, 9, "contains"),
    (2, 3, "contains"),
    (2, 4, "contains"),
    (3, 5, "contains"),
    (3, 6, "contains"),
    (2, 7, "declares_entry_point"),
    (2, 13, "declares_entry_point"),
    (9, 14, "declares_entry_point"),
    (7, 3, "implemented_by"),
    (8, 3, "physically_contains"),
    (2, 10, "used_by"),
    (2, 11, "used_by"),
    (5, 6, "calls"),
    (3, 4, "imports"),
    (4, 15, "contains"),
    (9, 16, "contains"),
    (16, 17, "contains"),
)


def _seed(db_path: Path) -> None:
    conn = raw_conn(db_path, create=True)
    try:
        with conn:
            for node_id, kind, name, path, line, attrs, uri in _NODES:
                conn.execute(
                    "INSERT INTO nodes (id, kind, name, path, line, attrs_json, uri, repo) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        node_id,
                        kind,
                        name,
                        path,
                        line,
                        json.dumps(attrs) if attrs is not None else None,
                        uri,
                        f"repo:{_ORG}/{_REPO}",
                    ),
                )
            conn.executemany("INSERT INTO edges (src, dst, kind) VALUES (?, ?, ?)", _EDGES)
    finally:
        conn.close()


@pytest.fixture
def graph_dir(tmp_path: Path) -> Path:
    """A directory holding a seeded `code.db`."""
    directory = tmp_path / "graph"
    directory.mkdir()
    _seed(directory / "code.db")
    return directory


@pytest.fixture
def empty_graph_dir(tmp_path: Path) -> Path:
    """A directory with no `code.db` — the NOT_INITIALIZED(3) path."""
    directory = tmp_path / "empty-graph"
    directory.mkdir()
    return directory


@pytest.fixture
def stale_graph_dir(tmp_path: Path) -> Path:
    """A seeded graph stamped at an old schema — the SCHEMA_MISMATCH(4) path."""
    directory = tmp_path / "stale-graph"
    directory.mkdir()
    _seed(directory / "code.db")
    conn = sqlite3.connect(directory / "code.db")
    try:
        with conn:
            conn.execute("UPDATE metadata SET value = '1' WHERE key = 'schema_version'")
    finally:
        conn.close()
    return directory

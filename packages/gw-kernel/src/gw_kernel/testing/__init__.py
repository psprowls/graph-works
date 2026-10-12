"""Shared test-only pytest fixtures for kernel and vertical-slice consumers.

Import explicitly in consumer conftests, including dependencies of code_layout.
Requires pytest from the test environment; ordinary kernel imports do not load it.
Graph seed data matches the real emitters and query identities.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Protocol

import pytest
from code_graph_io.testing import raw_graph_conn

from gw_kernel.workspace.init import apply_init, plan_init
from gw_kernel.workspace.layout import WorkspaceLayout

__all__ = ["code_layout", "declare_repos", "empty_graph_dir", "git_repo", "graph_dir", "stale_graph_dir"]


class _GitRepo(Protocol):
    def __call__(
        self, root: Path, files: dict[str, str | bytes], *, untracked: dict[str, str] | None = None
    ) -> Path: ...


class _DeclareRepos(Protocol):
    def __call__(
        self,
        layout: WorkspaceLayout,
        repos: dict[str, tuple[Path, list[str]]],
        ignore: list[str] | None = None,
    ) -> None: ...


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
        f"dependency:{_ORG}/{_REPO}:pypi:requests",
        None,
        {"ecosystem": "pypi", "versions_in_use": ["2.31.0"]},
        f"dependency:{_ORG}/{_REPO}/pypi/requests",
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


def _seed(graph_dir: Path) -> None:
    conn = raw_graph_conn(graph_dir=graph_dir, create=True)
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
    _seed(directory)
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
    _seed(directory)
    conn = raw_graph_conn(graph_dir=directory, create=False)
    try:
        with conn:
            conn.execute("UPDATE metadata SET value = '1' WHERE key = 'schema_version'")
    finally:
        conn.close()
    return directory


@pytest.fixture
def git_repo() -> _GitRepo:
    """Factory: a real git work tree at *root*, *files* staged (tracked), *untracked* written after."""

    def make(root: Path, files: dict[str, str | bytes], *, untracked: dict[str, str] | None = None) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8", newline="\n")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        for rel, content in (untracked or {}).items():
            (root / rel).write_text(content, encoding="utf-8", newline="\n")
        return root

    return make


@pytest.fixture
def declare_repos() -> _DeclareRepos:
    """Factory: replace the manifest's `repositories:`/`ignore:` tail with *repos* and a global *ignore*."""

    def declare(
        layout: WorkspaceLayout,
        repos: dict[str, tuple[Path, list[str]]],
        ignore: list[str] | None = None,
    ) -> None:
        head = layout.manifest_path.read_text(encoding="utf-8").split("repositories:")[0]
        lines = [head.rstrip("\n"), "repositories:"]
        for name, (path, own) in repos.items():
            lines += [f'  "{name}":', f'    path: "{path.as_posix()}"']
            if own:
                lines += ["    ignore:", *(f'      - "{glob}"' for glob in own)]
        if ignore:
            lines += ["ignore:", *(f'  - "{glob}"' for glob in ignore)]
        layout.manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    return declare


@pytest.fixture
def code_layout(tmp_path: Path, git_repo: _GitRepo, declare_repos: _DeclareRepos) -> WorkspaceLayout:
    """A workspace layout declaring one repository, `code`, a git checkout whose only tracked file is `src/a.py`.

    Shared by every core test directory (workspace, proposals) that resolves `path:N` citations.
    """
    host = tmp_path / "host"
    (host / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(host / ".works", today=date(2026, 9, 19), topic="Code")).layout
    code = git_repo(tmp_path / "code", {"src/a.py": "".join(f"a{n}\n" for n in range(1, 21))})
    declare_repos(layout, {"code": (code, [])})
    return layout

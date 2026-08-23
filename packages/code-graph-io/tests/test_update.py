"""Regression tests: per-node language stamping after a full graph build."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from _git_repo import init_repo, write_and_commit
from code_graph_io import packages, update
from code_graph_io.paths import graph_dir


def _open_ro(repo: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{graph_dir(repo) / 'code.db'}?mode=ro", uri=True)


@pytest.mark.integration
def test_run_workspace_discovers_each_member_manifest_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    init_repo(first)
    init_repo(second)
    write_and_commit(first, {"pyproject.toml": '[project]\nname = "first"\n'}, "init")
    write_and_commit(second, {"pyproject.toml": '[project]\nname = "second"\n'}, "init")

    original = packages.discover_manifest_packages
    discovered: list[Path] = []

    def record_discovery(repo_root: Path, *, ctx, ignore=None):
        discovered.append(repo_root.resolve())
        return original(repo_root, ctx=ctx, ignore=ignore)

    monkeypatch.setattr(packages, "discover_manifest_packages", record_discovery)

    update.run_workspace([first, second], graph_dir=graph_dir(tmp_path), full=True)

    assert discovered == [first.resolve(), second.resolve()]


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

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

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

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    conn = _open_ro(tmp_path)
    try:
        pkg_row = conn.execute("SELECT kind, attrs_json FROM nodes WHERE name='mypkg'").fetchone()
        assert pkg_row is not None, "mypkg node not found"
        kind, attrs_json = pkg_row
        assert kind == "package"
        assert json.loads(attrs_json)["language"] == "python"
    finally:
        conn.close()


@pytest.mark.integration
def test_csharp_solution_wired_into_full_update(tmp_path: Path) -> None:
    """Regression: csharp_projects is wired into the update pipeline.

    A full update over a repo with a .sln + .csproj produces a `solution`
    node with a `physically_contains` edge FROM the `repository` node
    (Task 8). A second full-mode pass must not delete the solution node
    (regression guard for Task 5's DELETE-exclusion fix).
    """
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "src/MyLib/MyLib.csproj": (
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                "<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>\n"
            ),
            "MyApp.sln": (
                "Microsoft Visual Studio Solution File, Format Version 12.00\n"
                "# Visual Studio Version 17\n"
                'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "MyLib", '
                '"src\\MyLib\\MyLib.csproj", "{EEEEEEEE-EEEE-EEEE-EEEE-EEEEEEEEEEEE}"\nEndProject\n'
            ),
        },
        "init",
    )

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    conn = _open_ro(tmp_path)
    try:
        sol_row = conn.execute("SELECT id, name FROM nodes WHERE kind='solution' AND name='MyApp'").fetchone()
        assert sol_row is not None, "solution node not found"
        sol_id, _ = sol_row

        repo_row = conn.execute("SELECT id FROM nodes WHERE kind='repository'").fetchone()
        assert repo_row is not None, "repository node not found"
        repo_id = repo_row[0]

        edge = conn.execute(
            "SELECT 1 FROM edges WHERE src = ? AND dst = ? AND kind = 'physically_contains'",
            (repo_id, sol_id),
        ).fetchone()
        assert edge is not None, "expected repository -physically_contains-> solution edge"
    finally:
        conn.close()

    # Second full-mode pass: the solution node must survive (regression guard
    # for Task 5's DELETE-exclusion fix in `_update_one_repo`'s full-mode
    # cleanup, which excludes kind='solution').
    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

    conn = _open_ro(tmp_path)
    try:
        sol_row = conn.execute("SELECT id FROM nodes WHERE kind='solution' AND name='MyApp'").fetchone()
        assert sol_row is not None, "solution node was deleted by a second full-mode rebuild"
    finally:
        conn.close()


@pytest.mark.integration
def test_csharp_package_node_id_stable_across_runs(tmp_path: Path) -> None:
    """Regression: packages._prune_vanished must not delete C#-sourced package
    rows -- it deletes them and csharp_projects.refresh immediately re-inserts
    them with a new row id, so the id churns on every full-mode pass
    (observed 5 -> 11) and every edge touching the node is cascade-rebuilt.
    """
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "src/Core/Core.csproj": (
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                "<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>\n"
            ),
        },
        "init",
    )

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)
    conn = _open_ro(tmp_path)
    try:
        first_id = conn.execute("SELECT id FROM nodes WHERE kind='package' AND name='Core'").fetchone()[0]
    finally:
        conn.close()

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)
    conn = _open_ro(tmp_path)
    try:
        second_id = conn.execute("SELECT id FROM nodes WHERE kind='package' AND name='Core'").fetchone()[0]
    finally:
        conn.close()

    assert first_id == second_id


@pytest.mark.integration
def test_full_update_with_csharp_using_and_project_completes(tmp_path: Path) -> None:
    """A C# using must not introduce a null-path import stub into builtins.refresh."""
    init_repo(tmp_path)
    write_and_commit(
        tmp_path,
        {
            "App.csproj": '<Project Sdk="Microsoft.NET.Sdk" />\n',
            "Program.cs": "using System;\nclass Program {}\n",
        },
        "init",
    )

    update.run(tmp_path, graph_dir=graph_dir(tmp_path), full=True)

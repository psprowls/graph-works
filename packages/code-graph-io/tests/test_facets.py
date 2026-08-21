"""Cross-cutting facet-model regression coverage.

Distinct from the per-kind unit tests in test_packages.py / test_agent_plugins.py:
this file exercises BOTH derived kinds (app, agent_plugin) together in one fixture
repo, confirms `facet_of` edges resolve correctly for both, and locks in the
edge-ownership rule (containment/dependency edges always source from the Package
node, never the App node) as an explicit regression guard.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from code_graph_io import agent_plugins, packages, store, upsert
from code_graph_io.queries import list_agent_plugins, list_apps, list_packages
from code_graph_io.records import GraphNode, GraphRecords
from code_graph_io.uri import RepoContext

_CTX = RepoContext(org="test", repo="repo")


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "code.db"
    c = store.connect(db, create=True)
    yield c
    c.close()


def _seed_file_node(conn: sqlite3.Connection, path: str) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="file",
                    name=path,
                    path=path,
                    line=None,
                    attrs={"language": "python"},
                )
            ],
            edges=[],
        ),
    )


def test_both_derived_kinds_reachable_from_package_lane(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A fixture repo with an entry-point-bearing app AND a manifest-bearing
    plugin root: both are reachable via list_packages(), and both still
    produce their own derived-kind node too."""
    app_dir = tmp_path / "apps" / "cli-tool"
    app_dir.mkdir(parents=True)
    (app_dir / "pyproject.toml").write_text(
        '[project]\nname = "cli-tool"\nversion = "0.1.0"\n[project.scripts]\ncli-tool = "cli_tool.main:run"\n'
    )

    plugin_dir = tmp_path / "plugins" / "demo"
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "demo-plugin"}))
    (plugin_dir / "pyproject.toml").write_text('[project]\nname = "demo-plugin"\nversion = "0.1.0"\n')

    packages.refresh(conn, repo_root=tmp_path, ctx=_CTX)
    agent_plugins.emit(conn, repo_root=tmp_path, ctx=_CTX)
    agent_plugins.link_agent_plugin_facets(conn, repo_root=tmp_path, ctx=_CTX)

    package_names = {n.name for n in list_packages(conn)}
    app_names = {n.name for n in list_apps(conn)}
    plugin_names = {n.name for n in list_agent_plugins(conn)}

    assert "cli-tool" in package_names
    assert "cli-tool" in app_names
    assert "demo-plugin" in package_names
    assert "demo-plugin" in plugin_names


def test_facet_of_resolves_for_both_derived_kinds(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """facet_of edges resolve to the correct target kind for each derived
    kind: Package -> App for an entry-point-bearing package, Package ->
    agent_plugin for a manifest-bearing plugin root — never crossed."""
    app_dir = tmp_path / "cli-tool"
    app_dir.mkdir(parents=True)
    (app_dir / "pyproject.toml").write_text(
        '[project]\nname = "cli-tool"\nversion = "0.1.0"\n[project.scripts]\ncli-tool = "x:y"\n'
    )
    plugin_dir = tmp_path / "demo"
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "demo-plugin"}))
    (plugin_dir / "pyproject.toml").write_text('[project]\nname = "demo-plugin"\nversion = "0.1.0"\n')

    packages.refresh(conn, repo_root=tmp_path, ctx=_CTX)
    agent_plugins.emit(conn, repo_root=tmp_path, ctx=_CTX)
    agent_plugins.link_agent_plugin_facets(conn, repo_root=tmp_path, ctx=_CTX)

    def _facet_targets(pkg_name: str) -> set[str]:
        rows = conn.execute(
            "SELECT d.kind FROM edges e "
            "JOIN nodes s ON e.src = s.id JOIN nodes d ON e.dst = d.id "
            "WHERE e.kind='facet_of' AND s.kind='package' AND s.name = ?",
            (pkg_name,),
        ).fetchall()
        return {r[0] for r in rows}

    assert _facet_targets("cli-tool") == {"app"}
    assert _facet_targets("demo-plugin") == {"agent_plugin"}


def test_faceted_member_edges_all_source_from_package(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """Regression guard: for a dual-facet member, used_by/depends_on_package/
    contains edges must source exclusively from the Package node, never the
    App node. `depends_on_package` needs an INTERNAL (workspace) dependency
    to fire at all (packages.py only emits it for workspace-to-workspace
    deps, never external ones) — myapp depends on a sibling workspace
    package for exactly that reason."""
    internal_dir = tmp_path / "internal-lib"
    internal_dir.mkdir(parents=True)
    (internal_dir / "pyproject.toml").write_text('[project]\nname = "internal-lib"\nversion = "0.1.0"\n')

    pkg_dir = tmp_path / "myapp"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\nversion = "0.1.0"\ndependencies = ["click", "internal-lib"]\n'
        '[project.scripts]\nmyapp = "myapp.cli:main"\n'
    )
    _seed_file_node(conn, "myapp/src/cli.py")
    packages.refresh(conn, repo_root=tmp_path, ctx=_CTX)

    for edge_kind in ("used_by", "depends_on_package", "contains"):
        src_kinds = {
            r[0]
            for r in conn.execute(
                "SELECT s.kind FROM edges e JOIN nodes s ON e.src = s.id WHERE e.kind=? AND s.name='myapp'",
                (edge_kind,),
            ).fetchall()
        }
        assert src_kinds, f"expected at least one {edge_kind} edge sourced from 'myapp' to exist"
        assert src_kinds == {"package"}, f"{edge_kind} edges must source only from the Package node, got {src_kinds}"

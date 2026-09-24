"""Dependency-facet reconciliation graph tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from code_graph_io import dependencies, upsert
from code_graph_io.packages import ManifestDependency, ManifestPackage
from code_graph_io.records import GraphEdge, GraphNode, as_graph_records
from code_graph_io.uri import RepoContext, pkg_uri, repo_uri


@pytest.fixture()
def conn(empty_db: sqlite3.Connection) -> sqlite3.Connection:
    return empty_db


def dep(ecosystem: str, name: str, *, spec: str = "", dev: bool = False) -> ManifestDependency:
    return ManifestDependency(ecosystem=ecosystem, name=name, spec=spec, dev=dev)  # type: ignore[arg-type]


def manifest(
    name: str,
    *,
    ecosystem: str = "pypi",
    dependencies: tuple[ManifestDependency, ...] = (),
    repo: str = "repo",
    relative_path: str | None = None,
    distributable: bool = True,
    app_kind: str | None = None,
) -> ManifestPackage:
    context = RepoContext(org="test", repo=repo)
    rel = relative_path if relative_path is not None else f"packages/{name}"
    return ManifestPackage(
        repo_root=Path("/workspace") / repo,
        repo=context,
        package_dir=Path("/workspace") / repo / rel,
        relative_path=rel,
        name=name,
        ecosystem=ecosystem,  # type: ignore[arg-type]
        version="1.0.0",
        description="",
        dependencies=dependencies,
        distributable=distributable,
        language="python" if ecosystem == "pypi" else "javascript",
        app_kind=app_kind,
        app_signals=(),
    )


def seed_packages(conn: sqlite3.Connection, manifests: tuple[ManifestPackage, ...]) -> None:
    upsert.upsert_records(
        conn,
        as_graph_records(
            nodes=(
                GraphNode(
                    kind="package",
                    name=item.name,
                    path=item.relative_path,
                    line=None,
                    attrs={"uri": pkg_uri(item.repo, item.name)},
                )
                for item in manifests
                if item.distributable
            )
        ),
    )


def _edge_triples(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    return set(
        conn.execute(
            "SELECT src.kind, edges.kind, dst.kind "
            "FROM edges JOIN nodes src ON src.id=edges.src "
            "JOIN nodes dst ON dst.id=edges.dst"
        ).fetchall()
    )


def test_unconsumed_distributable_has_no_dependency_node(conn: sqlite3.Connection) -> None:
    manifests = (manifest("library"),)
    seed_packages(conn, manifests)

    dependencies.reconcile_dependencies(conn, manifests=manifests, virtual_repository_dependencies={})

    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='dependency'").fetchone()[0] == 0
    assert _edge_triples(conn) == set()


def test_shared_external_dependency_is_one_node_per_repository(conn: sqlite3.Connection) -> None:
    manifests = (
        manifest("alpha", repo="a", dependencies=(dep("pypi", "requests", spec=">=2.30"),)),
        manifest("beta", repo="b", dependencies=(dep("pypi", "requests", spec="==2.31.0"),)),
    )
    seed_packages(conn, manifests)

    dependencies.reconcile_dependencies(conn, manifests=manifests, virtual_repository_dependencies={})

    rows = conn.execute("SELECT uri, repo, attrs_json FROM nodes WHERE kind='dependency' ORDER BY uri").fetchall()
    assert [(uri, repo) for uri, repo, _attrs in rows] == [
        ("dependency:test/a/pypi/requests", "repo:test/a"),
        ("dependency:test/b/pypi/requests", "repo:test/b"),
    ]
    assert [json.loads(attrs)["versions_in_use"] for _uri, _repo, attrs in rows] == [
        ["requests>=2.30"],
        ["requests==2.31.0"],
    ]
    crossing = conn.execute(
        "SELECT COUNT(*) FROM edges e JOIN nodes src ON src.id=e.src JOIN nodes dst ON dst.id=e.dst "
        "WHERE e.kind='used_by' AND dst.kind='dependency' AND src.uri NOT LIKE 'pkg:' || substr(dst.repo, 6) || '/%'"
    ).fetchone()[0]
    assert crossing == 0


def test_workspace_package_consumed_by_two_repositories_has_two_implemented_nodes(conn: sqlite3.Connection) -> None:
    manifests = (
        manifest("library", repo="lib"),
        manifest("alpha", repo="a", dependencies=(dep("pypi", "library", spec=">=1"),)),
        manifest("beta", repo="b", dependencies=(dep("pypi", "library", spec=">=2"),)),
    )
    seed_packages(conn, manifests)

    dependencies.reconcile_dependencies(conn, manifests=manifests, virtual_repository_dependencies={})

    implemented = conn.execute(
        "SELECT src.uri, dst.uri FROM edges e JOIN nodes src ON src.id=e.src JOIN nodes dst ON dst.id=e.dst "
        "WHERE e.kind='implemented_by' ORDER BY src.uri"
    ).fetchall()
    assert implemented == [
        ("dependency:test/a/pypi/library", "pkg:test/lib/library"),
        ("dependency:test/b/pypi/library", "pkg:test/lib/library"),
    ]
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE uri='dependency:test/lib/pypi/library'").fetchone()[0] == 0


def test_scoped_npm_name_keeps_its_slash_in_uri_and_path(conn: sqlite3.Connection) -> None:
    manifests = (manifest("web", ecosystem="npm", repo="web", dependencies=(dep("npm", "@Babel/Core"),)),)
    seed_packages(conn, manifests)

    dependencies.reconcile_dependencies(conn, manifests=manifests, virtual_repository_dependencies={})

    assert conn.execute("SELECT uri, path, name FROM nodes WHERE kind='dependency'").fetchall() == [
        ("dependency:test/web/npm/@babel/core", "dependency:test/web:npm:@babel/core", "@babel/core")
    ]


def test_internal_package_dependency_emits_all_three_relationships(conn: sqlite3.Connection) -> None:
    manifests = (
        manifest("consumer", dependencies=(dep("pypi", "library"),)),
        manifest("library"),
    )
    seed_packages(conn, manifests)

    dependencies.reconcile_dependencies(conn, manifests=manifests, virtual_repository_dependencies={})

    assert {
        ("package", "used_by", "dependency"),
        ("package", "depends_on_package", "package"),
        ("dependency", "implemented_by", "package"),
    } <= _edge_triples(conn)


def test_external_dependency_has_consumer_but_no_implementation(conn: sqlite3.Connection) -> None:
    manifests = (manifest("consumer", dependencies=(dep("pypi", "requests"),)),)
    seed_packages(conn, manifests)

    dependencies.reconcile_dependencies(conn, manifests=manifests, virtual_repository_dependencies={})

    rows = conn.execute(
        "SELECT src.kind, edges.kind, dst.kind FROM edges "
        "JOIN nodes src ON src.id=edges.src JOIN nodes dst ON dst.id=edges.dst "
        "WHERE dst.uri='dependency:test/repo/pypi/requests'"
    ).fetchall()
    assert rows == [("package", "used_by", "dependency")]


def test_reconciliation_preserves_unrelated_dependency_records_and_relationships(conn: sqlite3.Connection) -> None:
    manifests = (manifest("library"),)
    seed_packages(conn, manifests)
    unrelated_dependency = ("dependency", "third-party", "dependency:custom:third-party")
    unrelated_consumer = ("agent_plugin", "foreign-consumer", "plugin.json")
    unrelated_provider = ("package", "foreign-provider", "foreign/provider")
    unrelated_target = ("package", "foreign-target", "foreign/target")
    upsert.upsert_records(
        conn,
        as_graph_records(
            nodes=(
                GraphNode(
                    kind="dependency",
                    name="third-party",
                    path="dependency:custom:third-party",
                    line=None,
                    attrs={"uri": "dependency:custom/third-party"},
                ),
                GraphNode(kind="agent_plugin", name="foreign-consumer", path="plugin.json", line=None, attrs={}),
                GraphNode(
                    kind="package",
                    name="foreign-provider",
                    path="foreign/provider",
                    line=None,
                    attrs={"uri": "pkg:foreign/provider"},
                ),
                GraphNode(
                    kind="package",
                    name="foreign-target",
                    path="foreign/target",
                    line=None,
                    attrs={"uri": "pkg:foreign/target"},
                ),
            ),
            edges=(
                GraphEdge(src=unrelated_consumer, dst=unrelated_dependency, kind="used_by", attrs={}),
                GraphEdge(src=unrelated_dependency, dst=unrelated_provider, kind="implemented_by", attrs={}),
                GraphEdge(src=unrelated_provider, dst=unrelated_target, kind="depends_on_package", attrs={}),
            ),
        ),
    )

    dependencies.reconcile_dependencies(conn, manifests=manifests, virtual_repository_dependencies={})

    assert conn.execute("SELECT 1 FROM nodes WHERE uri='dependency:custom/third-party'").fetchone() is not None
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM edges JOIN nodes src ON src.id=edges.src JOIN nodes dst ON dst.id=edges.dst "
            "WHERE src.kind='agent_plugin' AND src.name='foreign-consumer' "
            "AND edges.kind='used_by' AND dst.uri='dependency:custom/third-party'"
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM edges JOIN nodes src ON src.id=edges.src JOIN nodes dst ON dst.id=edges.dst "
            "WHERE src.uri='dependency:custom/third-party' AND edges.kind='implemented_by' "
            "AND dst.uri='pkg:foreign/provider'"
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM edges JOIN nodes src ON src.id=edges.src JOIN nodes dst ON dst.id=edges.dst "
            "WHERE src.uri='pkg:foreign/provider' AND edges.kind='depends_on_package' "
            "AND dst.uri='pkg:foreign/target'"
        ).fetchone()[0]
        == 1
    )


def test_two_implementations_are_both_linked_to_one_dependency(conn: sqlite3.Connection) -> None:
    manifests = (
        manifest("library", repo="a", relative_path="a/library"),
        manifest("library", repo="b", relative_path="b/library"),
        manifest("consumer", repo="c", dependencies=(dep("pypi", "library"),)),
    )
    seed_packages(conn, manifests)

    dependencies.reconcile_dependencies(conn, manifests=manifests, virtual_repository_dependencies={})

    implemented = conn.execute(
        "SELECT dst.uri FROM edges JOIN nodes src ON src.id=edges.src JOIN nodes dst ON dst.id=edges.dst "
        "WHERE src.uri='dependency:test/c/pypi/library' AND edges.kind='implemented_by' ORDER BY dst.uri"
    ).fetchall()
    direct = conn.execute(
        "SELECT dst.uri FROM edges JOIN nodes src ON src.id=edges.src JOIN nodes dst ON dst.id=edges.dst "
        "WHERE src.uri='pkg:test/c/consumer' AND edges.kind='depends_on_package' ORDER BY dst.uri"
    ).fetchall()
    assert implemented == [("pkg:test/a/library",), ("pkg:test/b/library",)]
    assert direct == [("pkg:test/a/library",), ("pkg:test/b/library",)]


def test_ecosystems_with_same_name_stay_separate(conn: sqlite3.Connection) -> None:
    manifests = (manifest("consumer", repo="mixed", dependencies=(dep("pypi", "shared"), dep("npm", "@Acme/Shared"))),)
    seed_packages(conn, manifests)

    dependencies.reconcile_dependencies(conn, manifests=manifests, virtual_repository_dependencies={})

    assert conn.execute("SELECT uri FROM nodes WHERE kind='dependency' ORDER BY uri").fetchall() == [
        ("dependency:test/mixed/npm/@acme/shared",),
        ("dependency:test/mixed/pypi/shared",),
    ]


def test_dependency_identity_survives_removal_of_one_display_variant(conn: sqlite3.Connection) -> None:
    variants = (
        manifest("alpha", dependencies=(dep("pypi", "Library"),)),
        manifest("beta", dependencies=(dep("pypi", "library"),)),
    )
    seed_packages(conn, variants)

    dependencies.reconcile_dependencies(conn, manifests=variants, virtual_repository_dependencies={})
    before = conn.execute("SELECT name, uri FROM nodes WHERE kind='dependency'").fetchall()

    dependencies.reconcile_dependencies(conn, manifests=variants[1:], virtual_repository_dependencies={})
    after = conn.execute("SELECT name, uri FROM nodes WHERE kind='dependency'").fetchall()

    assert before == [("library", "dependency:test/repo/pypi/library")]
    assert after == before


def test_lifecycle_removes_only_stale_relationships_and_orphaned_facets(conn: sqlite3.Connection) -> None:
    initial = (
        manifest("consumer", dependencies=(dep("pypi", "library"),)),
        manifest("library"),
    )
    seed_packages(conn, initial)
    dependencies.reconcile_dependencies(conn, manifests=initial, virtual_repository_dependencies={})

    remaining = (manifest("consumer", dependencies=(dep("pypi", "library"),)),)
    dependencies.reconcile_dependencies(conn, manifests=remaining, virtual_repository_dependencies={})
    assert conn.execute("SELECT 1 FROM nodes WHERE uri='dependency:test/repo/pypi/library'").fetchone() is not None
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM edges JOIN nodes dst ON dst.id=edges.dst "
            "WHERE edges.kind='implemented_by' AND dst.uri='pkg:test/repo/library'"
        ).fetchone()[0]
        == 0
    )

    dependencies.reconcile_dependencies(conn, manifests=(), virtual_repository_dependencies={})
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='dependency'").fetchone()[0] == 0


def test_guarded_stale_dependency_remains_owned_until_it_becomes_orphaned(conn: sqlite3.Connection) -> None:
    manifests = (manifest("consumer", dependencies=(dep("pypi", "library"),)), manifest("library"))
    seed_packages(conn, manifests)
    dependencies.reconcile_dependencies(conn, manifests=manifests, virtual_repository_dependencies={})
    conn.execute(
        "INSERT INTO nodes(kind, name, path, line, attrs_json, uri, repo) "
        "VALUES ('agent_plugin', 'observer', 'observer', NULL, NULL, 'agent_plugin:test/repo/observer', NULL)"
    )
    conn.execute(
        "INSERT INTO edges(src, dst, kind, attrs_json) "
        "SELECT observer.id, dependency.id, 'observes', NULL "
        "FROM nodes observer, nodes dependency "
        "WHERE observer.uri='agent_plugin:test/repo/observer' "
        "AND dependency.uri='dependency:test/repo/pypi/library'"
    )

    dependencies.reconcile_dependencies(conn, manifests=(), virtual_repository_dependencies={})
    assert conn.execute("SELECT 1 FROM nodes WHERE uri='dependency:test/repo/pypi/library'").fetchone() is not None

    conn.execute("DELETE FROM edges WHERE kind='observes'")
    dependencies.reconcile_dependencies(conn, manifests=(), virtual_repository_dependencies={})

    assert conn.execute("SELECT 1 FROM nodes WHERE uri='dependency:test/repo/pypi/library'").fetchone() is None


def test_app_package_participates_but_unrepresented_plugin_does_not(conn: sqlite3.Connection) -> None:
    manifests = (manifest("consumer", dependencies=(dep("pypi", "app"),)), manifest("app", app_kind="cli"))
    seed_packages(conn, manifests)
    upsert.upsert_records(
        conn,
        as_graph_records(
            nodes=(GraphNode(kind="agent_plugin", name="standalone", path="plugin.json", line=None, attrs={}),)
        ),
    )

    dependencies.reconcile_dependencies(conn, manifests=manifests, virtual_repository_dependencies={})

    assert conn.execute("SELECT uri FROM nodes WHERE kind='dependency'").fetchall() == [
        ("dependency:test/repo/pypi/app",)
    ]
    assert conn.execute(
        "SELECT dst.uri FROM edges e JOIN nodes src ON src.id=e.src JOIN nodes dst ON dst.id=e.dst "
        "WHERE e.kind='implemented_by' AND src.uri='dependency:test/repo/pypi/app'"
    ).fetchall() == [("pkg:test/repo/app",)]


def test_virtual_dependencies_are_sourced_from_repository(conn: sqlite3.Connection) -> None:
    virtual = manifest("workspace", distributable=False, repo="workspace")
    virtual_repo_uri = repo_uri(virtual.repo)
    upsert.upsert_records(
        conn,
        as_graph_records(
            nodes=(GraphNode(kind="repository", name="workspace", path="", line=None, attrs={"uri": virtual_repo_uri}),)
        ),
    )

    dependencies.reconcile_dependencies(
        conn,
        manifests=(virtual,),
        virtual_repository_dependencies={virtual.repo: (dep("pypi", "pytest"),)},
    )

    assert conn.execute("SELECT repo FROM nodes WHERE kind='dependency'").fetchall() == [(virtual_repo_uri,)]
    assert conn.execute(
        "SELECT src.kind, edges.kind, dst.kind FROM edges "
        "JOIN nodes src ON src.id=edges.src JOIN nodes dst ON dst.id=edges.dst "
        "WHERE src.uri=?",
        (virtual_repo_uri,),
    ).fetchall() == [("repository", "used_by", "dependency")]


@pytest.mark.parametrize(
    ("ecosystem", "raw", "expected"),
    [
        ("pypi", "code-graph-io", "code-graph-io"),
        ("pypi", "code_graph_io", "code-graph-io"),
        ("pypi", "Code_Graph.IO", "code-graph-io"),
        ("pypi", "ruamel.yaml", "ruamel-yaml"),
        ("pypi", "zope..interface", "zope-interface"),
        ("pypi", "boto3", "boto3"),
        ("npm", "React", "react"),
        ("npm", "@scope/pkg", "@scope/pkg"),
    ],
)
def test_normalize_name_follows_pep503_for_pypi_and_lowercase_for_npm(ecosystem: str, raw: str, expected: str) -> None:
    """PyPI identity is the PEP 503 distribution name, not the import name.

    The import form (`code_graph_io`) is a different transform for a different
    purpose and lives in `import_scan`; conflating them is what filed
    Dependency pages under a name PyPI does not use.
    """
    assert dependencies._normalize_name(ecosystem, raw) == expected


def test_normalize_name_still_refuses_an_unsupported_ecosystem() -> None:
    with pytest.raises(ValueError, match="unsupported dependency ecosystem"):
        dependencies._normalize_name("cargo", "serde")


def test_registry_url_is_canonical_for_a_normalized_pypi_name() -> None:
    assert (
        dependencies._registry_url("pypi", dependencies._normalize_name("pypi", "code_graph_io"))
        == "https://pypi.org/project/code-graph-io/"
    )


def test_two_spellings_of_one_distribution_collapse_to_one_version_entry(conn: sqlite3.Connection) -> None:
    """`versions_in_use` is keyed by the node, so it must speak the node's
    normalized name -- otherwise one dependency accumulates an entry per
    spelling its consumers happened to use.
    """
    manifests = (
        manifest("alpha", dependencies=(dep("pypi", "Ruamel.YAML", spec=">=0.18"),)),
        manifest("beta", dependencies=(dep("pypi", "ruamel-yaml", spec=">=0.18"),)),
    )
    seed_packages(conn, manifests)

    dependencies.reconcile_dependencies(
        conn,
        manifests=manifests,
        virtual_repository_dependencies={},
    )

    attrs = json.loads(
        conn.execute("SELECT attrs_json FROM nodes WHERE uri='dependency:test/repo/pypi/ruamel-yaml'").fetchone()[0]
    )
    assert attrs["versions_in_use"] == ["ruamel-yaml>=0.18"]

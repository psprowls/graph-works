"""Manifest scanning: pyproject.toml + package.json → kind:package / kind:app nodes."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from code_graph_io import dependencies, packages, store, upsert
from code_graph_io.records import GraphEdge, GraphNode, GraphRecords
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


def _reconcile_dependencies(conn: sqlite3.Connection, tmp_path: Path) -> None:
    manifests = packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    dependencies.reconcile_dependencies(conn, manifests=manifests, virtual_repository_dependencies={})


def test_manifest_discovery_preserves_distribution_contract(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "py-dist"\ndependencies = ["HTTPX>=0.27"]\n')
    web = tmp_path / "web"
    web.mkdir()
    (web / "package.json").write_text(
        json.dumps(
            {
                "name": "@acme/web",
                "private": True,
                "dependencies": {"react": "^19"},
                "devDependencies": {"vite": "^7"},
            }
        )
    )

    found = packages.discover_manifest_packages(tmp_path, ctx=_CTX)

    assert [(item.name, item.ecosystem, item.distributable) for item in found] == [
        ("py-dist", "pypi", True),
        ("@acme/web", "npm", True),
    ]
    assert found[0].dependencies == (
        packages.ManifestDependency(ecosystem="pypi", name="httpx", spec=">=0.27", dev=False),
    )
    assert found[1].dependencies == (
        packages.ManifestDependency(ecosystem="npm", name="react", spec="^19", dev=False),
        packages.ManifestDependency(ecosystem="npm", name="vite", spec="^7", dev=True),
    )


def test_manifest_package_discovery_marks_uv_virtual_projects_non_distributable(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "workspace"\ndependencies = ["pytest>=8"]\n\n[tool.uv]\npackage = false\n'
    )

    found = packages.discover_manifest_packages(tmp_path, ctx=_CTX)

    assert found[0].distributable is False
    assert found[0].dependencies == (
        packages.ManifestDependency(ecosystem="pypi", name="pytest", spec=">=8", dev=False),
    )


def test_refresh_pyproject(tmp_path: Path, conn: sqlite3.Connection) -> None:
    pkg_dir = tmp_path / "packages" / "alpha"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text('[project]\nname = "alpha"\nversion = "0.1.1"\ndependencies = ["beta"]\n')
    _seed_file_node(conn, "packages/alpha/src/a.py")

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    row = conn.execute("SELECT name, attrs_json FROM nodes WHERE kind='package'").fetchone()
    assert row[0] == "alpha"
    attrs = json.loads(row[1])
    assert attrs["version"] == "0.1.1"
    assert attrs["dependencies"] == ["beta"]
    assert attrs["language"] == "python"


def test_refresh_package_json(tmp_path: Path, conn: sqlite3.Connection) -> None:
    pkg_dir = tmp_path / "frontend"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(
        json.dumps({"name": "frontend", "version": "1.0.0", "dependencies": {"x": "1"}})
    )

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    row = conn.execute("SELECT name, attrs_json FROM nodes WHERE kind='package'").fetchone()
    assert row[0] == "frontend"
    attrs = json.loads(row[1])
    assert attrs["language"] == "javascript"
    assert attrs["dependencies"] == ["x"]


def test_refresh_pyproject_stores_description(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """pyproject [project].description lands in attrs_json."""
    pkg_dir = tmp_path / "packages" / "alpha"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text(
        '[project]\nname = "alpha"\nversion = "0.1.1"\ndescription = "A test package."\n'
    )

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='package' AND name=?", ("alpha",)).fetchone()
    attrs = json.loads(row[0])
    assert attrs["description"] == "A test package."


def test_refresh_pyproject_absent_description_is_empty(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """no [project].description -> empty string, not a placeholder."""
    pkg_dir = tmp_path / "packages" / "beta"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text('[project]\nname = "beta"\nversion = "0.1.1"\n')

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='package' AND name=?", ("beta",)).fetchone()
    attrs = json.loads(row[0])
    assert attrs["description"] == ""


def test_refresh_creates_contains_edges(tmp_path: Path, conn: sqlite3.Connection) -> None:
    pkg_dir = tmp_path / "alpha"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text('[project]\nname = "alpha"\nversion = "0.1.1"\n')
    _seed_file_node(conn, "alpha/src/a.py")
    _seed_file_node(conn, "alpha/src/b.py")
    _seed_file_node(conn, "outside/c.py")

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    rows = conn.execute(
        "SELECT n2.name FROM edges e "
        "JOIN nodes n1 ON e.src=n1.id "
        "JOIN nodes n2 ON e.dst=n2.id "
        "WHERE n1.kind='package' AND n1.name='alpha' AND e.kind='contains'"
    ).fetchall()
    file_names = {row[0] for row in rows}
    assert file_names == {"alpha/src/a.py", "alpha/src/b.py"}


def test_refresh_does_not_contain_import_specifier_stubs(
    tmp_path: Path,
    conn: sqlite3.Connection,
) -> None:
    pkg_dir = tmp_path / "alpha"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text('[project]\nname = "alpha"\nversion = "0.1.1"\n')
    _seed_file_node(conn, "alpha/src/real.ts")
    conn.execute(
        "INSERT INTO nodes(kind, name, path, line, attrs_json, uri) "
        "VALUES ('file', 'ImportedSymbol', './real.js', NULL, NULL, NULL)"
    )

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    contained = {
        row[0]
        for row in conn.execute(
            "SELECT n2.path FROM edges e "
            "JOIN nodes n1 ON e.src=n1.id "
            "JOIN nodes n2 ON e.dst=n2.id "
            "WHERE n1.kind='package' AND n1.name='alpha' AND e.kind='contains'"
        ).fetchall()
    }
    assert contained == {"alpha/src/real.ts"}


def test_refresh_skips_venv_manifests(tmp_path: Path, conn: sqlite3.Connection) -> None:
    venv_pkg = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages" / "foo"
    venv_pkg.mkdir(parents=True)
    (venv_pkg / "pyproject.toml").write_text('[project]\nname = "foo"\nversion = "0.0.0"\n')

    real_pkg = tmp_path / "pkg"
    real_pkg.mkdir(parents=True)
    (real_pkg / "pyproject.toml").write_text('[project]\nname = "real-pkg"\nversion = "0.1.1"\n')

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    rows = conn.execute("SELECT name FROM nodes WHERE kind='package'").fetchall()
    names = {row[0] for row in rows}
    assert names == {"real-pkg"}
    assert "foo" not in names


def test_refresh_skips_broken_pyproject(tmp_path: Path, conn: sqlite3.Connection, capsys) -> None:
    pkg_dir = tmp_path / "alpha"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text("not valid toml [[[[")

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    captured = capsys.readouterr()
    assert "alpha" in captured.err or "pyproject.toml" in captured.err
    count = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='package'").fetchone()[0]
    assert count == 0


def test_refresh_writes_pkg_uri_on_package_nodes(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """every Package node has a non-NULL pkg:org/repo/name uri."""
    pkg_dir = tmp_path / "foo_pkg"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text('[project]\nname = "foo"\nversion = "0.1.1"\n')

    ctx = RepoContext("myorg", "myrepo")
    packages.refresh(
        conn, repo_root=tmp_path, ctx=ctx, manifests=packages.discover_manifest_packages(tmp_path, ctx=ctx)
    )

    row = conn.execute("SELECT uri, attrs_json FROM nodes WHERE kind='package' AND name='foo'").fetchone()
    assert row is not None
    uri, attrs_json = row
    assert uri == "pkg:myorg/myrepo/foo"
    # PITFALL 4 lock: uri must NOT leak into attrs_json.
    if attrs_json is not None:
        assert "uri" not in json.loads(attrs_json)


# ============================================================================
# dependency ingestion from [project.dependencies] +
# [dependency-groups], with used_by edges.
# ============================================================================


import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize(
    "spec, expected",
    [
        ("boto3>=1.38", "boto3"),
        ("langchain-aws[bedrock]>=1.4.6", "langchain-aws"),
        ("foo; python_version >= '3.11'", "foo"),
        ("foo", "foo"),
        ("Foo", "foo"),
        ("", None),
        ("git+https://example.com/x#egg=mypkg", None),
    ],
)
def test_pep_508_name_extraction(spec: str, expected: str | None) -> None:
    assert packages._extract_dep_name(spec) == expected


def test_dependency_ingestion_from_workspace(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """refresh emits dependency nodes + used_by edges across all manifests."""
    pkg_a = tmp_path / "pkg-a"
    pkg_a.mkdir()
    (pkg_a / "pyproject.toml").write_text(
        '[project]\nname = "pkg-a"\nversion = "0.1.1"\ndependencies = ["boto3>=1.38", "langchain-aws>=1.4"]\n'
    )
    pkg_b = tmp_path / "pkg-b"
    pkg_b.mkdir()
    (pkg_b / "pyproject.toml").write_text(
        '[project]\nname = "pkg-b"\nversion = "0.1.1"\n'
        'dependencies = ["boto3==1.40.0"]\n'
        '[dependency-groups]\ndev = ["pytest>=8"]\n'
    )

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    _reconcile_dependencies(conn, tmp_path)

    # Every distributable package and declared dependency has a facet.
    dep_rows = conn.execute("SELECT name, attrs_json, uri FROM nodes WHERE kind='dependency' ORDER BY name").fetchall()
    names = [r[0] for r in dep_rows]
    assert names == ["boto3", "langchain-aws", "pkg-a", "pkg-b", "pytest"]
    # boto3 attrs.versions_in_use collects both PEP 508 strings (sorted)
    boto3_row = next(row for row in dep_rows if row[0] == "boto3")
    boto3_attrs = json.loads(boto3_row[1])
    assert boto3_attrs["ecosystem"] == "pypi"
    assert boto3_attrs["url"] == "https://pypi.org/project/boto3/"
    assert boto3_attrs["versions_in_use"] == sorted(["boto3>=1.38", "boto3==1.40.0"])
    assert boto3_row[2] == "dependency:pypi/boto3"
    # used_by edges from both consumer packages to boto3
    boto3_used_by = conn.execute(
        "SELECT COUNT(*) FROM edges e "
        "JOIN nodes dep ON e.dst = dep.id "
        "WHERE e.kind='used_by' AND dep.kind='dependency' AND dep.name='boto3'"
    ).fetchone()[0]
    assert boto3_used_by == 2
    pytest_used_by = conn.execute(
        "SELECT COUNT(*) FROM edges e "
        "JOIN nodes dep ON e.dst = dep.id "
        "WHERE e.kind='used_by' AND dep.kind='dependency' AND dep.name='pytest'"
    ).fetchone()[0]
    assert pytest_used_by == 1


def test_used_by_edge_dedupes_per_consumer(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A dep listed in both [project.dependencies] and [dependency-groups] for the
    same consumer produces exactly ONE used_by edge from that consumer to that dep.
    """
    pkg_c = tmp_path / "pkg-c"
    pkg_c.mkdir()
    (pkg_c / "pyproject.toml").write_text(
        '[project]\nname = "pkg-c"\nversion = "0.1.1"\n'
        'dependencies = ["boto3>=1.38"]\n'
        '[dependency-groups]\nextra = ["boto3>=1.40"]\n'
    )
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    _reconcile_dependencies(conn, tmp_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='used_by' AND src.name='pkg-c' AND dst.name='boto3'"
    ).fetchone()[0]
    assert count == 1


# ============================================================================
# workspace-name suppression, the
# depends_on_package edge + retargeted used_by, and
# the external-dep regression.
# ============================================================================


def test_workspace_dep_suppressed_and_depends_on_package_emitted(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """Workspace-name suppression, depends_on_package, and retargeted used_by.

    `beta` declares workspace package `code-graph-io` (hyphen) whose own manifest
    name is `code_graph_io` (underscore) to exercise normalization, plus a
    genuine external dep `boto3`.
    """
    internal = tmp_path / "code_graph_io"
    internal.mkdir()
    (internal / "pyproject.toml").write_text('[project]\nname = "code_graph_io"\nversion = "0.1.1"\n')
    consumer = tmp_path / "beta"
    consumer.mkdir()
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "beta"\nversion = "0.1.1"\ndependencies = ["code-graph-io>=0.1", "boto3>=1.38"]\n'
    )

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    _reconcile_dependencies(conn, tmp_path)

    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE uri='dependency:pypi/code-graph-io'").fetchone()[0] == 1

    # Regression: the external dep STILL has a `dependency` node + used_by.
    boto3_node = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='dependency' AND name='boto3'").fetchone()[0]
    assert boto3_node == 1
    boto3_used_by = conn.execute(
        "SELECT COUNT(*) FROM edges e "
        "JOIN nodes dep ON e.dst = dep.id "
        "WHERE e.kind='used_by' AND dep.kind='dependency' AND dep.name='boto3'"
    ).fetchone()[0]
    assert boto3_used_by == 1

    # exactly one depends_on_package edge, src=beta dst=code_graph_io,
    # both endpoints resolving to package/app nodes (never `dependency`).
    dop_rows = conn.execute(
        "SELECT src.kind, src.name, dst.kind, dst.name FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='depends_on_package'"
    ).fetchall()
    assert len(dop_rows) == 1
    src_kind, src_name, dst_kind, dst_name = dop_rows[0]
    assert src_kind in ("package", "app") and src_name == "beta"
    assert dst_kind in ("package", "app") and dst_name == "code_graph_io"

    # The used_by edge always targets the Dependency facet, including internal
    # workspace packages.
    internal_used_by = conn.execute(
        "SELECT dst.kind FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='used_by' AND src.name='beta' AND dst.uri='dependency:pypi/code-graph-io'"
    ).fetchall()
    assert len(internal_used_by) == 1
    assert internal_used_by == [("dependency",)]


def test_internal_dep_edges_dedupe_per_consumer(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """dedupe: an internal package declared in both [project.dependencies]
    and a [dependency-groups] group yields exactly ONE used_by and ONE
    depends_on_package edge for that pair.
    """
    internal = tmp_path / "alpha"
    internal.mkdir()
    (internal / "pyproject.toml").write_text('[project]\nname = "alpha"\nversion = "0.1.1"\n')
    consumer = tmp_path / "beta"
    consumer.mkdir()
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "beta"\nversion = "0.1.1"\n'
        'dependencies = ["alpha>=0.1"]\n'
        '[dependency-groups]\ndev = ["alpha>=0.1"]\n'
    )

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    _reconcile_dependencies(conn, tmp_path)

    used_by_count = conn.execute(
        "SELECT COUNT(*) FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='used_by' AND src.name='beta' AND dst.uri='dependency:pypi/alpha'"
    ).fetchone()[0]
    assert used_by_count == 1
    dop_count = conn.execute(
        "SELECT COUNT(*) FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='depends_on_package' AND src.name='beta' AND dst.name='alpha'"
    ).fetchone()[0]
    assert dop_count == 1


def test_internal_dep_on_app_target_resolves_package_kind(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """stored-kind resolution: even when the internal target ALSO carries an
    App facet (has [project.scripts]), both edges' dst resolve to kind='package'
    — internal dependency edges always target the Package node, never its
    App sibling (facet model: contains/used_by/depends_on_package are
    Package-sourced and Package-targeted).
    """
    app_target = tmp_path / "mytool"
    app_target.mkdir()
    (app_target / "pyproject.toml").write_text(
        '[project]\nname = "mytool"\nversion = "0.1.1"\n[project.scripts]\nmytool = "mytool.cli:main"\n'
    )
    consumer = tmp_path / "beta"
    consumer.mkdir()
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "beta"\nversion = "0.1.1"\ndependencies = ["mytool>=0.1"]\n'
    )

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    _reconcile_dependencies(conn, tmp_path)

    for kind, expected_dst_kind in (("used_by", "dependency"), ("depends_on_package", "package")):
        dst_kind = conn.execute(
            "SELECT dst.kind FROM edges e "
            "JOIN nodes src ON e.src = src.id "
            "JOIN nodes dst ON e.dst = dst.id "
            "WHERE e.kind=? AND src.name='beta' AND (dst.name='mytool' OR dst.uri='dependency:pypi/mytool')",
            (kind,),
        ).fetchall()
        assert len(dst_kind) == 1, f"expected one {kind} edge to mytool"
        assert dst_kind[0][0] == expected_dst_kind


# ============================================================================
# scripts_present / bin_present manifest reader fields.
# ============================================================================


def test_read_pyproject_scripts_present_true_when_section_nonempty(tmp_path: Path) -> None:
    """[project.scripts] with at least one entry → scripts_present=True."""
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        '[project]\nname = "alpha"\nversion = "0.1.1"\n[project.scripts]\nalpha-cli = "alpha.cli:main"\n'
    )
    info = packages._read_pyproject(manifest)
    assert info is not None
    assert info["scripts_present"] is True
    # Legacy keys preserved.
    assert info["name"] == "alpha"
    assert info["version"] == "0.1.1"
    assert info["language"] == "python"
    assert info["dependencies"] == []
    assert info["dep_groups"] == {}


def test_read_pyproject_scripts_present_false_for_empty_or_missing(tmp_path: Path) -> None:
    """missing or empty [project.scripts] → scripts_present=False."""
    # Missing section.
    missing = tmp_path / "missing" / "pyproject.toml"
    missing.parent.mkdir()
    missing.write_text('[project]\nname = "alpha"\nversion = "0.1.1"\n')
    info_missing = packages._read_pyproject(missing)
    assert info_missing is not None
    assert info_missing["scripts_present"] is False

    # Empty table.
    empty = tmp_path / "empty" / "pyproject.toml"
    empty.parent.mkdir()
    empty.write_text('[project]\nname = "beta"\nversion = "0.1.1"\n[project.scripts]\n')
    info_empty = packages._read_pyproject(empty)
    assert info_empty is not None
    assert info_empty["scripts_present"] is False


def test_read_package_json_bin_present_for_string(tmp_path: Path) -> None:
    """bin as non-empty string → bin_present=True."""
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({"name": "myapp", "version": "1.0.0", "bin": "cli.js"}))
    info = packages._read_package_json(manifest)
    assert info is not None
    assert info["bin_present"] is True
    assert info["name"] == "myapp"
    assert info["language"] == "javascript"


def test_read_package_json_bin_present_for_dict(tmp_path: Path) -> None:
    """bin as dict with at least one truthy value → bin_present=True."""
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({"name": "myapp", "version": "1.0.0", "bin": {"foo": "bin/foo.js"}}))
    info = packages._read_package_json(manifest)
    assert info is not None
    assert info["bin_present"] is True


def test_read_package_json_bin_present_false_for_empty_dict(tmp_path: Path) -> None:
    """bin as empty dict → bin_present=False."""
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({"name": "myapp", "version": "1.0.0", "bin": {}}))
    info = packages._read_package_json(manifest)
    assert info is not None
    assert info["bin_present"] is False


def test_read_package_json_bin_present_false_when_missing(tmp_path: Path) -> None:
    """no bin key → bin_present=False."""
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({"name": "myapp", "version": "1.0.0"}))
    info = packages._read_package_json(manifest)
    assert info is not None
    assert info["bin_present"] is False
    # Legacy keys preserved.
    assert info["name"] == "myapp"
    assert info["version"] == "1.0.0"
    assert info["language"] == "javascript"
    assert info["dependencies"] == []


# ============================================================================
# facet coexistence: Package is unconditional, App is an additive facet.
# ============================================================================


def test_app_signals_add_a_facet_not_a_flip(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A package gaining [project.scripts] gets a SECOND node (App), linked
    to the first (Package) by a facet_of edge — the Package row is untouched."""
    pkg_dir = tmp_path / "myapp"
    pkg_dir.mkdir(parents=True)
    manifest = pkg_dir / "pyproject.toml"
    manifest.write_text('[project]\nname = "myapp"\nversion = "0.1.1"\n')
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    pkg_row = conn.execute("SELECT id, kind, uri FROM nodes WHERE name='myapp'").fetchone()
    assert pkg_row is not None
    pkg_id, pkg_kind, pkg_uri_val = pkg_row
    assert pkg_kind == "package"
    assert pkg_uri_val.startswith("pkg:")

    manifest.write_text('[project]\nname = "myapp"\nversion = "0.1.1"\n[project.scripts]\nmyapp = "myapp.cli:main"\n')
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    rows = conn.execute("SELECT id, kind, uri, attrs_json FROM nodes WHERE name='myapp' ORDER BY kind").fetchall()
    assert [r[1] for r in rows] == ["app", "package"], f"expected one app row and one package row; got {rows!r}"
    app_row, pkg_row_after = rows
    assert pkg_row_after[0] == pkg_id, "Package row id must be untouched by the facet gaining an App sibling"
    assert pkg_row_after[1] == "package"
    assert pkg_row_after[2].startswith("pkg:")
    app_attrs = json.loads(app_row[3])
    assert app_attrs["app_kind"] == "cli"
    assert app_attrs["app_signals"] == ["cli"]
    pkg_attrs = json.loads(pkg_row_after[3])
    assert "app_kind" not in pkg_attrs
    assert "app_signals" not in pkg_attrs

    facet_edge = conn.execute(
        "SELECT src, dst FROM edges e "
        "JOIN nodes s ON e.src = s.id JOIN nodes d ON e.dst = d.id "
        "WHERE e.kind='facet_of' AND s.name='myapp' AND d.name='myapp'"
    ).fetchone()
    assert facet_edge is not None
    src_kind = conn.execute("SELECT kind FROM nodes WHERE id=?", (facet_edge[0],)).fetchone()[0]
    dst_kind = conn.execute("SELECT kind FROM nodes WHERE id=?", (facet_edge[1],)).fetchone()[0]
    assert (src_kind, dst_kind) == ("package", "app")


def test_app_facet_pruned_when_signals_disappear_package_survives(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """Losing [project.scripts] prunes the App node; the Package node (and
    its id) survive untouched — no more in-place flip-to-package."""
    pkg_dir = tmp_path / "myapp"
    pkg_dir.mkdir(parents=True)
    manifest = pkg_dir / "pyproject.toml"
    manifest.write_text('[project]\nname = "myapp"\nversion = "0.1.1"\n[project.scripts]\nmyapp = "myapp.cli:main"\n')
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    pkg_row = conn.execute("SELECT id FROM nodes WHERE name='myapp' AND kind='package'").fetchone()
    assert pkg_row is not None
    pkg_id = pkg_row[0]
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE name='myapp' AND kind='app'").fetchone()[0] == 1

    manifest.write_text('[project]\nname = "myapp"\nversion = "0.1.1"\n')
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    rows = conn.execute("SELECT id, kind FROM nodes WHERE name='myapp'").fetchall()
    assert [r[1] for r in rows] == ["package"], f"App facet should be pruned; got {rows!r}"
    assert rows[0][0] == pkg_id, "surviving Package row id must be unchanged"
    assert conn.execute("SELECT COUNT(*) FROM edges WHERE kind='facet_of'").fetchone()[0] == 0


def test_app_facet_edges_do_not_survive_kind_flip_fk(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """An inbound edge against the Package row survives its App sibling
    being pruned (cascade only removes edges touching the deleted App row)."""
    pkg_dir = tmp_path / "myapp"
    pkg_dir.mkdir(parents=True)
    manifest = pkg_dir / "pyproject.toml"
    manifest.write_text('[project]\nname = "myapp"\nversion = "0.1.1"\n[project.scripts]\nmyapp = "myapp.cli:main"\n')
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    upsert._upsert_edge(
        conn,
        GraphEdge(
            src=("test_suite", "billing-tests", None),
            dst=("package", "myapp", "myapp"),
            kind="tests",
            attrs={},
        ),
    )
    pkg_id = conn.execute("SELECT id FROM nodes WHERE name='myapp' AND kind='package'").fetchone()[0]
    inbound_before = conn.execute("SELECT COUNT(*) FROM edges WHERE dst=?", (pkg_id,)).fetchone()[0]
    assert inbound_before == 1

    manifest.write_text('[project]\nname = "myapp"\nversion = "0.1.1"\n')
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    assert conn.execute("SELECT id FROM nodes WHERE name='myapp' AND kind='package'").fetchone()[0] == pkg_id
    inbound_after = conn.execute("SELECT COUNT(*) FROM edges WHERE dst=?", (pkg_id,)).fetchone()[0]
    assert inbound_after == 1, "the unrelated inbound edge on the surviving Package row must be untouched"


def test_no_kind_flip_for_zero_signal_manifest(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """zero-signal pure library re-run does not duplicate or flip the row."""
    pkg_dir = tmp_path / "purelib"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text('[project]\nname = "purelib"\nversion = "0.1.1"\n')
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    rows_before = conn.execute("SELECT id, kind, uri FROM nodes WHERE name='purelib'").fetchall()
    assert len(rows_before) == 1
    pkg_id, kind_before, uri_before = rows_before[0]
    assert kind_before == "package"

    # Re-run with identical manifest — no flip should occur.
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    rows_after = conn.execute("SELECT id, kind, uri FROM nodes WHERE name='purelib'").fetchall()
    assert len(rows_after) == 1, "zero-signal re-run must not duplicate the row"
    assert rows_after[0] == (pkg_id, kind_before, uri_before)


# ============================================================================
# JS-signal and multi-signal integration tests.
# ============================================================================


def _refresh_and_fetch(
    tmp_path: Path, conn: sqlite3.Connection, name: str, *, kind: str = "package"
) -> tuple[str, str, dict]:
    """Run packages.refresh and return (kind, uri, attrs) for the named row of
    the given `kind`. Under the facet model a manifest with app signals gets
    BOTH a package row and an app row sharing `name`, so callers that mean to
    inspect the App facet must pass kind="app" explicitly."""
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    row = conn.execute("SELECT kind, uri, attrs_json FROM nodes WHERE name=? AND kind=?", (name, kind)).fetchone()
    assert row is not None, f"no {kind!r} row named {name!r} after refresh"
    return row[0], row[1], json.loads(row[2]) if row[2] else {}


def test_refresh_js_bin_string_classifies_app_cli(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """package.json bin as non-empty string → app/cli."""
    pkg_dir = tmp_path / "tool"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text(json.dumps({"name": "tool", "version": "1.0.0", "bin": "cli.js"}))
    kind, uri, attrs = _refresh_and_fetch(tmp_path, conn, "tool", kind="app")
    assert kind == "app"
    assert uri.startswith("app:")
    assert attrs["app_kind"] == "cli"
    assert attrs["app_signals"] == ["cli"]


def test_refresh_js_bin_dict_classifies_app_cli(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """package.json bin as dict with truthy value → app/cli."""
    pkg_dir = tmp_path / "tool"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "tool",
                "version": "1.0.0",
                "bin": {"foo": "bin/foo.js"},
            }
        )
    )
    kind, uri, attrs = _refresh_and_fetch(tmp_path, conn, "tool", kind="app")
    assert kind == "app"
    assert uri.startswith("app:")
    assert attrs["app_kind"] == "cli"
    assert attrs["app_signals"] == ["cli"]


def test_refresh_js_next_classifies_app_nextjs(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """dependencies.next present → app/nextjs."""
    pkg_dir = tmp_path / "site"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "site",
                "version": "1.0.0",
                "dependencies": {"next": "14", "react": "18"},
            }
        )
    )
    kind, uri, attrs = _refresh_and_fetch(tmp_path, conn, "site", kind="app")
    assert kind == "app"
    assert uri.startswith("app:")
    assert attrs["app_kind"] == "nextjs"
    assert "nextjs" in attrs["app_signals"]


def test_refresh_js_expo_classifies_app_expo(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """dependencies.expo present → app/expo."""
    pkg_dir = tmp_path / "mobile"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "mobile",
                "version": "1.0.0",
                "dependencies": {"expo": "50", "react-native": "0.73"},
            }
        )
    )
    kind, _uri, attrs = _refresh_and_fetch(tmp_path, conn, "mobile", kind="app")
    assert kind == "app"
    assert attrs["app_kind"] == "expo"
    assert "expo" in attrs["app_signals"]


def test_refresh_js_vite_with_index_html_classifies_app_spa(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """vite dep AND index.html on disk → app/spa."""
    pkg_dir = tmp_path / "spa-app"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "spa-app",
                "version": "1.0.0",
                "dependencies": {"vite": "5", "react": "18"},
            }
        )
    )
    (pkg_dir / "index.html").write_text("<!doctype html><html></html>")
    kind, _uri, attrs = _refresh_and_fetch(tmp_path, conn, "spa-app", kind="app")
    assert kind == "app"
    assert attrs["app_kind"] == "spa"
    assert "spa" in attrs["app_signals"]


def test_refresh_js_vite_without_index_html_stays_package(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """vite dep WITHOUT index.html → no spa signal → stays package."""
    pkg_dir = tmp_path / "lib"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "lib",
                "version": "1.0.0",
                "dependencies": {"vite": "5"},
            }
        )
    )
    kind, uri, attrs = _refresh_and_fetch(tmp_path, conn, "lib")
    assert kind == "package"
    assert uri.startswith("pkg:")
    assert "app_kind" not in attrs
    assert "app_signals" not in attrs


def test_refresh_js_multi_signal_nextjs_wins(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """multi-signal precedence — bin + next → app_kind=nextjs, sorted signals."""
    pkg_dir = tmp_path / "site"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "site",
                "version": "1.0.0",
                "bin": "cli.js",
                "dependencies": {"next": "14"},
            }
        )
    )
    kind, _uri, attrs = _refresh_and_fetch(tmp_path, conn, "site", kind="app")
    assert kind == "app"
    assert attrs["app_kind"] == "nextjs"
    assert attrs["app_signals"] == sorted(["cli", "nextjs"])


def test_refresh_python_pure_library_stays_package(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """pyproject without [project.scripts] → kind=package, no app keys."""
    pkg_dir = tmp_path / "purelib"
    pkg_dir.mkdir()
    (pkg_dir / "pyproject.toml").write_text('[project]\nname = "purelib"\nversion = "0.1.1"\n')
    kind, uri, attrs = _refresh_and_fetch(tmp_path, conn, "purelib")
    assert kind == "package"
    assert uri.startswith("pkg:")
    assert "app_kind" not in attrs
    assert "app_signals" not in attrs


def test_refresh_app_node_attrs_json_contains_app_kind_and_signals(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """app rows expose app_kind/app_signals via json_extract; package rows expose NULL."""
    # App: pyproject with scripts.
    app_dir = tmp_path / "myapp"
    app_dir.mkdir()
    (app_dir / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\nversion = "0.1.1"\n[project.scripts]\nmyapp = "myapp.cli:main"\n'
    )
    # Package: pyproject without scripts.
    pkg_dir = tmp_path / "purelib"
    pkg_dir.mkdir()
    (pkg_dir / "pyproject.toml").write_text('[project]\nname = "purelib"\nversion = "0.1.1"\n')

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    app_row = conn.execute(
        "SELECT json_extract(attrs_json, '$.app_kind'), "
        "       json_extract(attrs_json, '$.app_signals') "
        "FROM nodes WHERE name='myapp' AND kind='app'"
    ).fetchone()
    assert app_row[0] == "cli"
    assert app_row[1] is not None

    # myapp's Package facet (the sibling row created alongside the App facet)
    # must NOT carry app_kind / app_signals either.
    myapp_pkg_row = conn.execute(
        "SELECT json_extract(attrs_json, '$.app_kind'), "
        "       json_extract(attrs_json, '$.app_signals') "
        "FROM nodes WHERE name='myapp' AND kind='package'"
    ).fetchone()
    assert myapp_pkg_row[0] is None
    assert myapp_pkg_row[1] is None

    pkg_row = conn.execute(
        "SELECT json_extract(attrs_json, '$.app_kind'), "
        "       json_extract(attrs_json, '$.app_signals') "
        "FROM nodes WHERE name='purelib'"
    ).fetchone()
    assert pkg_row[0] is None
    assert pkg_row[1] is None


# ============================================================================
# devDependencies merge + electron classification + dev_dependencies attr
# ============================================================================


def test_refresh_electron_app_from_dev_deps(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """package.json with electron+vite under devDependencies + index.html →
    kind='app', app_kind='electron', merged deps list + dev_dependencies attr."""
    app_dir = tmp_path / "apps" / "app-electron-ts"
    app_dir.mkdir(parents=True)
    (app_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "app-electron-ts",
                "version": "1.0.0",
                "devDependencies": {"electron": "^30.0.0", "vite": "^5.0.0"},
            }
        )
    )
    (app_dir / "index.html").write_text("<!doctype html><html></html>")

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    row = conn.execute("SELECT kind, attrs_json FROM nodes WHERE name='app-electron-ts' AND kind='app'").fetchone()
    assert row is not None
    kind, attrs_json = row
    assert kind == "app"
    attrs = json.loads(attrs_json)
    assert attrs["app_kind"] == "electron"
    # merged deps list contains both dev deps (sorted)
    assert attrs["dependencies"] == ["electron", "vite"]
    # dev_dependencies marker surfaces the dev-origin names
    assert attrs["dev_dependencies"] == ["electron", "vite"]


def test_refresh_js_dev_dep_marker_splits_runtime_vs_dev(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """runtime dep + dev-only dep → merged list contains both,
    dev_dependencies contains only the dev one."""
    pkg_dir = tmp_path / "myapp"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "myapp",
                "version": "1.0.0",
                "dependencies": {"react": "^18"},
                "devDependencies": {"vite": "^5.0.0"},
            }
        )
    )

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    row = conn.execute("SELECT attrs_json FROM nodes WHERE name='myapp'").fetchone()
    assert row is not None
    attrs = json.loads(row[0])
    # merged: both react (runtime) and vite (dev), sorted
    assert attrs["dependencies"] == ["react", "vite"]
    # dev marker: only vite came from devDependencies
    assert attrs["dev_dependencies"] == ["vite"]


def test_refresh_python_package_dev_dependencies_empty(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """Python manifests have no devDependencies → dev_dependencies attr is []."""
    pkg_dir = tmp_path / "pypkg"
    pkg_dir.mkdir()
    (pkg_dir / "pyproject.toml").write_text('[project]\nname = "pypkg"\nversion = "0.1.1"\ndependencies = ["boto3"]\n')

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    row = conn.execute("SELECT attrs_json FROM nodes WHERE name='pypkg'").fetchone()
    assert row is not None
    attrs = json.loads(row[0])
    assert attrs["dev_dependencies"] == []


# ============================================================================
# JS npm dependency-parity integration tests
# (comprehensive; mirror test_dependency_ingestion_from_workspace for Python)
# ============================================================================


def test_js_npm_dependency_parity_full_monorepo(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """k5y T3: full JS dep-parity integration — external runtime/dev deps + internal workspace.

    Monorepo layout:
      jspkg/ — consumer with runtime react, dev vitest, internal jslib
      jslib/  — sibling workspace package
    """
    # Internal sibling package
    lib_dir = tmp_path / "jslib"
    lib_dir.mkdir()
    (lib_dir / "package.json").write_text(json.dumps({"name": "jslib", "version": "1.0.0"}))
    # Consumer package
    app_dir = tmp_path / "jspkg"
    app_dir.mkdir()
    (app_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "jspkg",
                "version": "2.0.0",
                "dependencies": {"react": "^18.2.0", "jslib": "workspace:*"},
                "devDependencies": {"vitest": "^1.0.0"},
            }
        )
    )

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    _reconcile_dependencies(conn, tmp_path)

    # 1. react → dependency node with ecosystem=npm, correct URI, versions_in_use
    react_row = conn.execute(
        "SELECT name, attrs_json, uri FROM nodes WHERE kind='dependency' AND name='react'"
    ).fetchone()
    assert react_row is not None, "react dependency node should be emitted"
    react_attrs = json.loads(react_row[1])
    assert react_attrs["ecosystem"] == "npm"
    assert react_attrs["url"] == "https://www.npmjs.com/package/react"
    assert react_row[2] == "dependency:npm/react"
    assert "^18.2.0" in react_attrs["versions_in_use"]

    # used_by edge from jspkg to react
    react_edge = conn.execute(
        "SELECT e.attrs_json FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='used_by' AND src.name='jspkg' AND dst.name='react'"
    ).fetchone()
    assert react_edge is not None, "used_by edge jspkg->react must exist"
    react_edge_attrs = json.loads(react_edge[0]) if react_edge[0] else {}

    # exactly one used_by edge from jspkg to react (COUNT(*), not just presence)
    react_edge_count = conn.execute(
        "SELECT COUNT(*) FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='used_by' AND src.name='jspkg' AND dst.name='react'"
    ).fetchone()[0]
    assert react_edge_count == 1

    # vitest → dependency node + used_by edge with dev marker
    vitest_row = conn.execute("SELECT name, attrs_json FROM nodes WHERE kind='dependency' AND name='vitest'").fetchone()
    assert vitest_row is not None, "vitest dependency node should be emitted"

    vitest_edge = conn.execute(
        "SELECT e.attrs_json FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='used_by' AND src.name='jspkg' AND dst.name='vitest'"
    ).fetchone()
    assert vitest_edge is not None, "used_by edge jspkg->vitest must exist"
    vitest_edge_attrs = json.loads(vitest_edge[0]) if vitest_edge[0] else {}

    # Dependency relationship ownership does not encode a source-group marker.
    assert vitest_edge_attrs == {}
    assert react_edge_attrs == {}

    # 3. jslib (internal) owns a Dependency facet and a direct package edge.
    jslib_dep_count = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='dependency' AND name='jslib'").fetchone()[0]
    assert jslib_dep_count == 1

    dop_rows = conn.execute(
        "SELECT src.name, dst.name FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind='depends_on_package'"
    ).fetchall()
    assert len(dop_rows) == 1, f"exactly one depends_on_package edge expected; got {dop_rows}"
    assert dop_rows[0] == ("jspkg", "jslib")


# ============================================================================
# Git-ignored manifest exclusion
# ============================================================================


@pytest.mark.parametrize("manifest_name", ["package.json", "pyproject.toml"])
def test_manifest_discovery_skips_git_ignored_manifests(tmp_path: Path, manifest_name: str) -> None:
    """A manifest under a git-ignored path is not a package of the repository.

    The structural lane already enumerates tracked files via `git ls-files`
    (structural_nodes._tracked_files); the manifest lane must agree with it —
    a package.json/pyproject.toml that exists on disk under a git-ignored
    directory (e.g. `tmp/`) must not be discovered as a package.
    """

    def write_manifest(path: Path, name: str) -> None:
        if manifest_name == "package.json":
            path.write_text(json.dumps({"name": name, "version": "1.0.0"}))
        else:
            path.write_text(f'[project]\nname = "{name}"\nversion = "1.0.0"\n')

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)

    write_manifest(tmp_path / manifest_name, "tracked-pkg")
    (tmp_path / ".gitignore").write_text("tmp/\n")

    ignored_dir = tmp_path / "tmp" / "ignored-pkg"
    ignored_dir.mkdir(parents=True)
    write_manifest(ignored_dir / manifest_name, "ignored-pkg")

    subprocess.run(["git", "add", manifest_name, ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    manifests = packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    names = {m.name for m in manifests}

    assert "tracked-pkg" in names, f"tracked manifest missing from discovery: {names}"
    assert "ignored-pkg" not in names, f"git-ignored manifest was discovered as a package: {names}"


@pytest.mark.parametrize("manifest_name", ["package.json", "pyproject.toml"])
def test_manifest_discovery_falls_back_on_empty_git_index(tmp_path: Path, manifest_name: str) -> None:
    """A git repo with nothing staged/committed yet must not zero out discovery.

    `git ls-files` succeeds with empty output in a freshly `git init`'d repo
    (or one with commits but nothing added), which is NOT the same as
    `NotInGitRepoError`. The structural lane treats an empty tracked list as
    "fall back to the filesystem walk" (`structural_nodes.py` — `if not
    tracked:`); the manifest lane must agree with it rather than filtering
    out every manifest.
    """
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)

    if manifest_name == "package.json":
        (tmp_path / manifest_name).write_text(json.dumps({"name": "unstaged-pkg", "version": "1.0.0"}))
    else:
        (tmp_path / manifest_name).write_text('[project]\nname = "unstaged-pkg"\nversion = "1.0.0"\n')
    # Deliberately no `git add` / `git commit` — the index stays empty.

    manifests = packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    names = {m.name for m in manifests}

    assert "unstaged-pkg" in names, f"manifest lost to an empty git index: {names}"


def test_js_versions_in_use_aggregates_across_consumers(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """k5y T3: multiple JS consumers of the same dep → versions_in_use collects all specs."""
    pkg_a = tmp_path / "pkg-a"
    pkg_a.mkdir()
    (pkg_a / "package.json").write_text(
        json.dumps(
            {
                "name": "pkg-a",
                "version": "1.0.0",
                "dependencies": {"react": "^17.0.0"},
            }
        )
    )
    pkg_b = tmp_path / "pkg-b"
    pkg_b.mkdir()
    (pkg_b / "package.json").write_text(
        json.dumps(
            {
                "name": "pkg-b",
                "version": "1.0.0",
                "dependencies": {"react": "^18.2.0"},
            }
        )
    )

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    _reconcile_dependencies(conn, tmp_path)

    react_attrs_json = conn.execute("SELECT attrs_json FROM nodes WHERE kind='dependency' AND name='react'").fetchone()[
        0
    ]
    react_attrs = json.loads(react_attrs_json)
    versions = react_attrs["versions_in_use"]
    assert "^17.0.0" in versions
    assert "^18.2.0" in versions

    # Two used_by edges (one per consumer)
    react_used_by = conn.execute(
        "SELECT COUNT(*) FROM edges e JOIN nodes dst ON e.dst = dst.id WHERE e.kind='used_by' AND dst.name='react'"
    ).fetchone()[0]
    assert react_used_by == 2


# ============================================================================
# _read_package_json returns dep_specs name->spec map
# ============================================================================


def test_read_package_json_dep_specs_runtime_and_dev(tmp_path: Path) -> None:
    """k5y T1: dep_specs covers runtime + dev deps; runtime version wins on name collision."""
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "mypkg",
                "version": "1.0.0",
                "dependencies": {"react": "^18.2.0", "lodash": "^4.17.21"},
                "devDependencies": {"vitest": "^1.0.0", "react": "^17.0.0"},  # react in both — runtime wins
            }
        )
    )
    info = packages._read_package_json(manifest)
    assert info is not None
    dep_specs = info["dep_specs"]
    # All four names present
    assert set(dep_specs.keys()) == {"react", "lodash", "vitest"}
    # runtime version wins for react (not the dev ^17.0.0)
    assert dep_specs["react"] == "^18.2.0"
    assert dep_specs["lodash"] == "^4.17.21"
    assert dep_specs["vitest"] == "^1.0.0"
    # Existing fields unchanged
    assert info["dependencies"] == sorted(["react", "lodash", "vitest"])
    # dev_dependencies is raw devDeps keys (react appears in both — it's listed in dev_deps)
    assert "vitest" in info["dev_dependencies"]
    assert "react" in info["dev_dependencies"]


def test_read_package_json_dep_specs_empty_when_no_deps(tmp_path: Path) -> None:
    """k5y T1: dep_specs is empty dict when no dependencies declared."""
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({"name": "bare", "version": "1.0.0"}))
    info = packages._read_package_json(manifest)
    assert info is not None
    assert info["dep_specs"] == {}


def test_read_package_json_dep_specs_coerces_non_string_spec(tmp_path: Path) -> None:
    """k5y T1: non-string spec values are coerced to '' rather than raising."""
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "mypkg",
                "version": "1.0.0",
                "dependencies": {"react": 18, "lodash": None},
            }
        )
    )
    info = packages._read_package_json(manifest)
    assert info is not None
    # Should not raise; non-string specs become ""
    assert "react" in info["dep_specs"]
    assert "lodash" in info["dep_specs"]
    assert isinstance(info["dep_specs"]["react"], str)
    assert isinstance(info["dep_specs"]["lodash"], str)


# ============================================================================
# Plugin-root manifests now ALSO get a Package node (spec decision reversed)
# ============================================================================


def test_plugin_root_manifest_also_gets_a_package_node(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A pyproject.toml AT a .claude-plugin/ dir now IS emitted as a package
    (facet model: every manifest gets a Package node, unconditionally)."""
    pdir = tmp_path / "plugins" / "demo"
    (pdir / ".claude-plugin").mkdir(parents=True)
    (pdir / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "demo"}))
    (pdir / "pyproject.toml").write_text('[project]\nname = "demo-plugin-pkg"\nversion = "0"\n')
    nested = pdir / "scripts" / "helper"
    nested.mkdir(parents=True)
    (nested / "pyproject.toml").write_text('[project]\nname = "demo-helper"\nversion = "0"\n')

    ctx = RepoContext(org="t", repo="r")
    packages.refresh(
        conn, repo_root=tmp_path, ctx=ctx, manifests=packages.discover_manifest_packages(tmp_path, ctx=ctx)
    )
    names = {r[0] for r in conn.execute("SELECT name FROM nodes WHERE kind='package'").fetchall()}
    assert "demo-plugin-pkg" in names
    assert "demo-helper" in names


# ============================================================================
# regression-lock node language + defensive package dominant-language
# ============================================================================


def _seed_file_with_lang(conn: sqlite3.Connection, path: str, language: str | None) -> None:
    """Insert a bare file node with optional language in attrs_json."""
    attrs = {"language": language} if language else {}
    conn.execute(
        "INSERT INTO nodes (kind, name, path, line, attrs_json) VALUES ('file', ?, ?, NULL, ?)",
        (path, path, json.dumps(attrs) if attrs else None),
    )


def test_dominant_language_majority(conn: sqlite3.Connection) -> None:
    from code_graph_io.packages import _dominant_language

    _seed_file_with_lang(conn, "packages/x/a.py", "python")
    _seed_file_with_lang(conn, "packages/x/b.py", "python")
    _seed_file_with_lang(conn, "packages/x/c.ts", "typescript")
    assert _dominant_language(conn, ["packages/x/a.py", "packages/x/b.py", "packages/x/c.ts"], None) == "python"


def test_dominant_language_tie_returns_none(conn: sqlite3.Connection) -> None:
    from code_graph_io.packages import _dominant_language

    _seed_file_with_lang(conn, "packages/y/a.py", "python")
    _seed_file_with_lang(conn, "packages/y/b.ts", "typescript")
    assert _dominant_language(conn, ["packages/y/a.py", "packages/y/b.ts"], None) is None


def test_dominant_language_no_languages_returns_none(conn: sqlite3.Connection) -> None:
    from code_graph_io.packages import _dominant_language

    _seed_file_with_lang(conn, "packages/z/readme.md", None)
    assert _dominant_language(conn, ["packages/z/readme.md"], None) is None


def test_refresh_app_node_carries_language(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """Regression: a pyproject app node must carry language='python' in attrs_json."""
    pkg_dir = tmp_path / "packages" / "appy"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text(
        '[project]\nname = "appy"\nversion = "0.1.0"\n[project.scripts]\nappy = "appy:main"\n'
    )
    _seed_file_node(conn, "packages/appy/src/appy/__init__.py")
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    # appy has [project.scripts] → also gets an App facet alongside its Package node.
    row = conn.execute("SELECT kind, attrs_json FROM nodes WHERE name='appy' AND kind='app'").fetchone()
    assert row is not None
    kind, attrs_json = row
    assert kind == "app"
    assert json.loads(attrs_json)["language"] == "python"


def test_refresh_falls_back_to_dominant_language_when_manifest_silent(
    tmp_path: Path, conn: sqlite3.Connection, monkeypatch
) -> None:
    """End-to-end: when the manifest reader declares no language, refresh() infers
    the package node's language from the dominant language of its contained files.

    The manifest reader is monkeypatched to blank the language so we drive the
    defensive fallback branch in refresh() (normal builds never reach it because
    both manifest readers always set a language)."""
    pkg_dir = tmp_path / "silentpkg"
    pkg_dir.mkdir()
    (pkg_dir / "pyproject.toml").write_text('[project]\nname = "silentpkg"\nversion = "0.1.0"\n')
    # Contained file nodes with a clear dominant language (python: 2, typescript: 1).
    _seed_file_with_lang(conn, "silentpkg/a.py", "python")
    _seed_file_with_lang(conn, "silentpkg/b.py", "python")
    _seed_file_with_lang(conn, "silentpkg/c.ts", "typescript")

    real_reader = packages._read_pyproject

    def _silent_reader(path):
        info = real_reader(path)
        if info is not None:
            info["language"] = ""  # simulate a manifest type that declares no language
        return info

    monkeypatch.setattr(packages, "_read_pyproject", _silent_reader)

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='package' AND name='silentpkg'").fetchone()
    assert row is not None
    assert json.loads(row[0])["language"] == "python"


def test_refresh_prunes_package_whose_manifest_vanished(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A package node whose manifest disappears between two --full-style
    refresh() calls is deleted on the second call, not left stale forever."""
    pkg_dir = tmp_path / "gone"
    pkg_dir.mkdir()
    manifest = pkg_dir / "pyproject.toml"
    manifest.write_text('[project]\nname = "gone"\nversion = "0.1.0"\n')

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    assert conn.execute("SELECT 1 FROM nodes WHERE kind='package' AND name='gone'").fetchone() is not None

    manifest.unlink()
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    assert conn.execute("SELECT 1 FROM nodes WHERE kind='package' AND name='gone'").fetchone() is None


def test_refresh_prune_leaves_surviving_packages_alone(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """Pruning a vanished manifest must not touch a sibling package that is
    still discovered."""
    gone_dir = tmp_path / "gone"
    gone_dir.mkdir()
    manifest = gone_dir / "pyproject.toml"
    manifest.write_text('[project]\nname = "gone"\nversion = "0.1.0"\n')
    keep_dir = tmp_path / "keep"
    keep_dir.mkdir()
    (keep_dir / "pyproject.toml").write_text('[project]\nname = "keep"\nversion = "0.1.0"\n')

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    manifest.unlink()
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    assert conn.execute("SELECT 1 FROM nodes WHERE kind='package' AND name='gone'").fetchone() is None
    assert conn.execute("SELECT 1 FROM nodes WHERE kind='package' AND name='keep'").fetchone() is not None


def test_refresh_prune_cascades_edges(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A pruned package node's contains/used_by edges are cascade-deleted with
    it — no orphan edge rows survive."""
    pkg_dir = tmp_path / "gone"
    pkg_dir.mkdir()
    manifest = pkg_dir / "pyproject.toml"
    manifest.write_text('[project]\nname = "gone"\nversion = "0.1.0"\ndependencies = ["requests"]\n')
    _seed_file_node(conn, "gone/a.py")

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    node_id = conn.execute("SELECT id FROM nodes WHERE kind='package' AND name='gone'").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM edges WHERE src=? OR dst=?", (node_id, node_id)).fetchone()[0] > 0

    manifest.unlink()
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    assert conn.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone() is None
    assert conn.execute("SELECT COUNT(*) FROM edges WHERE src=? OR dst=?", (node_id, node_id)).fetchone()[0] == 0


def test_refresh_prune_scoped_to_current_repo(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A member's prune pass must only ever delete package/app nodes stamped
    with its own `repo` — never a sibling workspace member's (H3: unscoped
    pruning silently deletes the wrong repo's packages)."""
    member_a = tmp_path / "member_a"
    member_a.mkdir()
    (member_a / "pyproject.toml").write_text('[project]\nname = "a"\nversion = "0.1.0"\n')
    member_b = tmp_path / "member_b"
    member_b.mkdir()
    b_manifest = member_b / "pyproject.toml"
    b_manifest.write_text('[project]\nname = "b"\nversion = "0.1.0"\n')

    # Mirrors update._update_one_repo: the connection-scoped repo (which
    # stamps nodes.repo at insert time) is set around each member's refresh,
    # in step with the `current_repo` argument.
    upsert.set_current_repo(conn, "repo:a")
    packages.refresh(
        conn,
        repo_root=member_a,
        ctx=_CTX,
        manifests=packages.discover_manifest_packages(member_a, ctx=_CTX),
        current_repo="repo:a",
    )
    upsert.set_current_repo(conn, "repo:b")
    packages.refresh(
        conn,
        repo_root=member_b,
        ctx=_CTX,
        manifests=packages.discover_manifest_packages(member_b, ctx=_CTX),
        current_repo="repo:b",
    )
    upsert.set_current_repo(conn, None)
    assert conn.execute("SELECT 1 FROM nodes WHERE kind='package' AND name='a'").fetchone() is not None

    # member_b's manifest vanishes; only member_b's refresh runs again.
    b_manifest.unlink()
    upsert.set_current_repo(conn, "repo:b")
    packages.refresh(
        conn,
        repo_root=member_b,
        ctx=_CTX,
        manifests=packages.discover_manifest_packages(member_b, ctx=_CTX),
        current_repo="repo:b",
    )
    upsert.set_current_repo(conn, None)

    assert conn.execute("SELECT 1 FROM nodes WHERE kind='package' AND name='a'").fetchone() is not None
    assert conn.execute("SELECT 1 FROM nodes WHERE kind='package' AND name='b'").fetchone() is None


# ============================================================================
# `[tool.uv] package = false` — a virtual workspace root is not a Package
# ============================================================================


def test_virtual_root_emits_no_package_node(tmp_path: Path, conn: sqlite3.Connection) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "ws"\nversion = "0.0.0"\n[tool.uv]\npackage = false\n')
    alpha = tmp_path / "packages" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "pyproject.toml").write_text('[project]\nname = "alpha"\nversion = "0.1.0"\n')

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    names = {row[0] for row in conn.execute("SELECT name FROM nodes WHERE kind='package'").fetchall()}
    assert names == {"alpha"}
    contains = conn.execute(
        "SELECT COUNT(*) FROM edges e JOIN nodes n ON e.src = n.id WHERE n.name='ws' AND e.kind='contains'"
    ).fetchone()[0]
    assert contains == 0


def test_tool_uv_package_true_still_emits_a_package(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """Negative case: `package = true`, and a bare `[tool.uv]` with no `package`
    key, both still produce a Package node — guards the `is False` check
    against a truthiness regression."""
    true_dir = tmp_path / "true_pkg"
    true_dir.mkdir()
    (true_dir / "pyproject.toml").write_text(
        '[project]\nname = "true_pkg"\nversion = "0.1.0"\n[tool.uv]\npackage = true\n'
    )
    bare_dir = tmp_path / "bare_pkg"
    bare_dir.mkdir()
    (bare_dir / "pyproject.toml").write_text('[project]\nname = "bare_pkg"\nversion = "0.1.0"\n[tool.uv]\n')

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    names = {row[0] for row in conn.execute("SELECT name FROM nodes WHERE kind='package'").fetchall()}
    assert names == {"true_pkg", "bare_pkg"}


def test_virtual_root_has_no_package_or_dependency_side_effect_during_refresh(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "ws"\nversion = "0.0.0"\n'
        "[tool.uv]\npackage = false\n"
        '[dependency-groups]\ndev = ["mypy>=1.0"]\n'
    )
    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind IN ('package', 'dependency')").fetchone()[0] == 0


def test_virtual_root_internal_deps_are_not_linked_during_package_refresh(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "ws"\nversion = "0.0.0"\n[tool.uv]\npackage = false\n[dependency-groups]\ndev = ["alpha"]\n'
    )
    alpha = tmp_path / "packages" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "pyproject.toml").write_text('[project]\nname = "alpha"\nversion = "0.1.0"\n')
    packages.refresh(
        conn,
        repo_root=tmp_path,
        ctx=_CTX,
        manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX),
    )

    used_by = conn.execute(
        "SELECT COUNT(*) FROM edges e JOIN nodes d ON e.dst = d.id WHERE d.name='alpha' AND e.kind='used_by'"
    ).fetchone()[0]
    assert used_by == 0
    dop = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='depends_on_package'").fetchone()[0]
    assert dop == 0


def test_previously_admitted_root_is_pruned(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A `package` row from before the `virtual` fix existed is pruned on the
    next refresh — verifiable on an already-populated database."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[GraphNode(kind="package", name="ws", path="", line=None, attrs={"uri": "pkg:test/repo/ws"})],
            edges=[],
        ),
    )
    assert conn.execute("SELECT 1 FROM nodes WHERE kind='package' AND name='ws'").fetchone() is not None

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "ws"\nversion = "0.0.0"\n[tool.uv]\npackage = false\n')

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )

    assert conn.execute("SELECT 1 FROM nodes WHERE kind='package' AND name='ws'").fetchone() is None


def test_python_dep_group_edges_carry_dev_attr(tmp_path: Path, conn: sqlite3.Connection) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0.1.0"\ndependencies = ["requests"]\n[dependency-groups]\ndev = ["mypy"]\n'
    )

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    _reconcile_dependencies(conn, tmp_path)

    def _edge_attrs(dep_name: str) -> dict:
        row = conn.execute(
            "SELECT e.attrs_json FROM edges e JOIN nodes d ON e.dst = d.id WHERE d.name=? AND e.kind='used_by'",
            (dep_name,),
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else {}

    assert _edge_attrs("requests") == {}
    assert _edge_attrs("mypy") == {}


def test_runtime_dependency_wins_over_dep_group(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A name declared in both [project.dependencies] and a dep-group yields
    ONE edge with attrs={} — the runtime pass wins the first-write dedupe."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0.1.0"\ndependencies = ["mypy"]\n[dependency-groups]\ndev = ["mypy"]\n'
    )

    packages.refresh(
        conn, repo_root=tmp_path, ctx=_CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=_CTX)
    )
    _reconcile_dependencies(conn, tmp_path)

    rows = conn.execute(
        "SELECT e.attrs_json FROM edges e JOIN nodes d ON e.dst = d.id WHERE d.name='mypy' AND e.kind='used_by'"
    ).fetchall()
    assert len(rows) == 1
    attrs = json.loads(rows[0][0]) if rows[0][0] else {}
    assert attrs == {}


def test_dependency_registry_url_nuget() -> None:
    assert (
        packages._dependency_registry_url("nuget", "Newtonsoft.Json")
        == "https://www.nuget.org/packages/Newtonsoft.Json/"
    )


def test_dependency_registry_url_unsupported_still_raises() -> None:
    with pytest.raises(ValueError, match="unsupported dependency ecosystem"):
        packages._dependency_registry_url("gems", "rails")

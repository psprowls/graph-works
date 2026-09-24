"""Query layer: find, callers, callees, imports, describe_package, describe_path."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from code_graph_io import queries, resolve, store, upsert
from code_graph_io.records import GraphEdge, GraphNode, GraphRecords


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "code.db"
    c = store.connect(db, create=True)
    yield c
    c.close()


def _seed_file_node(conn: sqlite3.Connection, path: str) -> None:
    """Seed a bare File node so `packages.refresh`'s contains-edge loop
    (which reads existing File rows via `_file_nodes_under`) has something
    to link. Mirrors `test_packages.py`'s helper of the same name."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[GraphNode(kind="file", name=path, path=path, line=None, attrs={"language": "python"})],
            edges=[],
        ),
    )


def _seed_call_chain(conn: sqlite3.Connection) -> None:
    nodes = [
        GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
        GraphNode(kind="function", name="alpha", path="a.py", line=1, attrs={}),
        GraphNode(kind="function", name="beta", path="a.py", line=5, attrs={}),
        GraphNode(kind="function", name="gamma", path="a.py", line=10, attrs={}),
    ]
    edges = [
        GraphEdge(src=("function", "alpha", "a.py"), dst=("function", "beta", None), kind="calls", attrs={}),
        GraphEdge(src=("function", "beta", "a.py"), dst=("function", "gamma", None), kind="calls", attrs={}),
    ]
    upsert.upsert_records(conn, GraphRecords(nodes=nodes, edges=edges))
    resolve.sweep(conn)


def test_find_by_name(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="function", name="foo", path="a.py", line=1, attrs={}),
                GraphNode(kind="class", name="foo", path="b.py", line=2, attrs={}),
            ],
            edges=[],
        ),
    )
    rows = queries.find(conn, name="foo")
    assert {(r.kind, r.path) for r in rows} == {("function", "a.py"), ("class", "b.py")}


def test_find_by_name_and_kind(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="function", name="foo", path="a.py", line=1, attrs={}),
                GraphNode(kind="class", name="foo", path="b.py", line=2, attrs={}),
            ],
            edges=[],
        ),
    )
    rows = queries.find(conn, name="foo", kind="function")
    assert [(r.kind, r.path) for r in rows] == [("function", "a.py")]


def test_callers_depth_bounded(conn: sqlite3.Connection) -> None:
    _seed_call_chain(conn)
    rows = queries.callers(conn, name="gamma", depth=1)
    names = {r.name for r in rows}
    assert names == {"beta"}

    rows = queries.callers(conn, name="gamma", depth=3)
    names = {r.name for r in rows}
    assert names == {"alpha", "beta"}


def test_callees_depth_bounded(conn: sqlite3.Connection) -> None:
    _seed_call_chain(conn)
    rows = queries.callees(conn, name="alpha", depth=1)
    assert {r.name for r in rows} == {"beta"}

    rows = queries.callees(conn, name="alpha", depth=3)
    assert {r.name for r in rows} == {"beta", "gamma"}


def test_imports_returns_resolved_only(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
                GraphNode(kind="file", name="b.py", path="b.py", line=None, attrs={}),
            ],
            edges=[
                GraphEdge(src=("file", "a.py", "a.py"), dst=("file", "b.py", None), kind="imports", attrs={}),
                GraphEdge(src=("file", "a.py", "a.py"), dst=("file", "missing", None), kind="imports", attrs={}),
            ],
        ),
    )
    resolve.sweep(conn)
    rows = queries.imports(conn, path="a.py")
    assert [r.path for r in rows] == ["b.py"]


def test_describe_file_scopes_same_relative_path_by_uri(conn: sqlite3.Connection) -> None:
    for repo, package, symbol in (
        ("repo:acme/alpha", "alpha-pkg", "alpha_symbol"),
        ("repo:acme/beta", "beta-pkg", "beta_symbol"),
    ):
        upsert.set_current_repo(conn, repo)
        repo_payload = repo.removeprefix("repo:")
        upsert.upsert_records(
            conn,
            GraphRecords(
                nodes=[
                    GraphNode(
                        kind="package",
                        name=package,
                        path=None,
                        line=None,
                        attrs={"uri": f"pkg:{repo_payload}/{package}"},
                    ),
                    GraphNode(
                        kind="file",
                        name="src/shared.py",
                        path="src/shared.py",
                        line=None,
                        attrs={"uri": f"file:{repo_payload}/src/shared.py", "is_importable": True},
                    ),
                    GraphNode(kind="function", name=symbol, path="src/shared.py", line=1, attrs={}),
                ],
                edges=[
                    GraphEdge(
                        src=("package", package, None),
                        dst=("file", "src/shared.py", "src/shared.py"),
                        kind="contains",
                        attrs={},
                    ),
                    GraphEdge(
                        src=("file", "src/shared.py", "src/shared.py"),
                        dst=("function", symbol, "src/shared.py"),
                        kind="contains",
                        attrs={},
                    ),
                ],
            ),
        )

    description = queries.describe_file(conn, uri="file:acme/beta/src/shared.py")

    assert description is not None
    assert description.package == ("beta-pkg", "pkg:acme/beta/beta-pkg")
    assert [child.name for child in description.children] == ["beta_symbol"]


def test_describe_package(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="package",
                    name="alpha",
                    path="alpha",
                    line=None,
                    attrs={"language": "python", "version": "0.1.1"},
                ),
                GraphNode(kind="file", name="alpha/a.py", path="alpha/a.py", line=None, attrs={}),
                GraphNode(kind="function", name="foo", path="alpha/a.py", line=1, attrs={}),
            ],
            edges=[
                GraphEdge(
                    src=("package", "alpha", "alpha"),
                    dst=("file", "alpha/a.py", "alpha/a.py"),
                    kind="contains",
                    attrs={},
                ),
                GraphEdge(
                    src=("file", "alpha/a.py", "alpha/a.py"),
                    dst=("function", "foo", "alpha/a.py"),
                    kind="contains",
                    attrs={},
                ),
            ],
        ),
    )
    desc = queries.describe_package(conn, name="alpha")
    assert desc.name == "alpha"
    assert desc.language == "python"
    assert desc.version == "0.1.1"
    assert "alpha/a.py" in desc.files
    assert desc.counts["function"] == 1


def test_entity_descriptions_can_scope_duplicate_names_by_uri(conn: sqlite3.Connection) -> None:
    for repo, marker in (("one", "first"), ("two", "second")):
        repo_uri = f"repo:acme/{repo}"
        upsert.set_current_repo(conn, repo_uri)
        upsert.upsert_records(
            conn,
            GraphRecords(
                nodes=[
                    GraphNode(
                        kind="package",
                        name="shared",
                        path="shared/pyproject.toml",
                        line=None,
                        attrs={"uri": f"pkg:acme/{repo}/shared", "version": marker},
                    ),
                    GraphNode(
                        kind="file",
                        name="src/shared.py",
                        path="src/shared.py",
                        line=None,
                        attrs={"uri": f"file:acme/{repo}/src/shared.py"},
                    ),
                    GraphNode(kind="function", name=marker, path="src/shared.py", line=1, attrs={}),
                    GraphNode(
                        kind="package",
                        name="shared-app",
                        path="shared-app/package.json",
                        line=None,
                        attrs={"uri": f"pkg:acme/{repo}/shared-app"},
                    ),
                    GraphNode(
                        kind="app",
                        name="shared-app",
                        path="shared-app/app",
                        line=None,
                        attrs={
                            "uri": f"app:acme/{repo}/shared-app",
                            "version": marker,
                            "app_signals": [marker],
                        },
                    ),
                    GraphNode(
                        kind="file",
                        name="src/app.ts",
                        path="src/app.ts",
                        line=None,
                        attrs={"uri": f"file:acme/{repo}/src/app.ts"},
                    ),
                    GraphNode(kind="function", name=f"{marker}_app", path="src/app.ts", line=1, attrs={}),
                    GraphNode(
                        kind="test_suite",
                        name="shared-tests",
                        path="tests",
                        line=None,
                        attrs={
                            "uri": f"test_suite:acme/{repo}/shared-tests",
                            "suite_kind": marker,
                        },
                    ),
                    GraphNode(
                        kind="file",
                        name="tests/shared.py",
                        path="tests/shared.py",
                        line=None,
                        attrs={"uri": f"file:acme/{repo}/tests/shared.py"},
                    ),
                    GraphNode(
                        kind="agent_plugin",
                        name="shared-plugin",
                        path=".claude-plugin",
                        line=None,
                        attrs={
                            "uri": f"agent_plugin:acme/{repo}/shared-plugin",
                            "version": marker,
                            "components": {"commands": [{"name": marker}]},
                        },
                    ),
                ],
                edges=[
                    GraphEdge(
                        src=("package", "shared", "shared/pyproject.toml"),
                        dst=("file", "src/shared.py", "src/shared.py"),
                        kind="contains",
                        attrs={},
                    ),
                    GraphEdge(
                        src=("package", "shared-app", "shared-app/package.json"),
                        dst=("app", "shared-app", "shared-app/app"),
                        kind="facet_of",
                        attrs={},
                    ),
                    GraphEdge(
                        src=("package", "shared-app", "shared-app/package.json"),
                        dst=("file", "src/app.ts", "src/app.ts"),
                        kind="contains",
                        attrs={},
                    ),
                    GraphEdge(
                        src=("test_suite", "shared-tests", "tests"),
                        dst=("file", "tests/shared.py", "tests/shared.py"),
                        kind="physically_contains",
                        attrs={},
                    ),
                ],
            ),
        )
    upsert.set_current_repo(conn, None)

    package = queries.describe_package(conn, name="shared", uri="pkg:acme/two/shared")
    app = queries.describe_app(conn, name="shared-app", uri="app:acme/two/shared-app")
    suite = queries.describe_test_suite(
        conn,
        suite_name="shared-tests",
        uri="test_suite:acme/two/shared-tests",
    )
    plugin = queries.describe_agent_plugin(
        conn,
        name="shared-plugin",
        uri="agent_plugin:acme/two/shared-plugin",
    )

    assert package is not None
    assert package.version == "second"
    assert package.counts == {"function": 1}
    assert app is not None
    assert app.version == "second"
    assert app.app_signals == ["second"]
    assert app.counts == {"function": 1}
    assert suite is not None
    assert suite.kind == "second"
    assert suite.file_count == 1
    assert plugin is not None
    assert plugin.version == "second"
    assert plugin.commands == [{"name": "second"}]
    assert queries.describe_package(conn, name="shared", uri="pkg:acme/missing/shared") is None


def test_describe_package_internal_deps_and_dependents(
    conn: sqlite3.Connection,
) -> None:
    """describe_package surfaces both directions of the
    depends_on_package edge — incoming dependents and outgoing dependencies.
    """
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="package",
                    name="alpha",
                    path="alpha",
                    line=None,
                    attrs={"language": "python", "version": "0.1.1"},
                ),
                GraphNode(
                    kind="package",
                    name="beta",
                    path="beta",
                    line=None,
                    attrs={"language": "python", "version": "0.1.1"},
                ),
                GraphNode(
                    kind="package",
                    name="gamma",
                    path="gamma",
                    line=None,
                    attrs={"language": "python", "version": "0.1.1"},
                ),
            ],
            edges=[
                # beta depends on alpha (src=consumer, dst=internal package)
                GraphEdge(
                    src=("package", "beta", "beta"),
                    dst=("package", "alpha", "alpha"),
                    kind="depends_on_package",
                    attrs={},
                ),
            ],
        ),
    )
    # Incoming: alpha's dependents include beta.
    alpha = queries.describe_package(conn, name="alpha")
    assert alpha is not None
    assert alpha.internal_dependents == ["beta"]
    assert alpha.internal_dependencies == []
    # Outgoing: beta's dependencies include alpha.
    beta = queries.describe_package(conn, name="beta")
    assert beta is not None
    assert beta.internal_dependencies == ["alpha"]
    assert beta.internal_dependents == []
    # Edgeless package: both empty.
    gamma = queries.describe_package(conn, name="gamma")
    assert gamma is not None
    assert gamma.internal_dependencies == []
    assert gamma.internal_dependents == []


def test_internal_dependencies_of_package_and_app(
    conn: sqlite3.Connection,
) -> None:
    """internal_dependencies_of returns outgoing
    depends_on_package dst-names for BOTH package and app source nodes
    (describe_package is package-only), sorted, [] when none."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="package",
                    name="alpha",
                    path="alpha",
                    line=None,
                    attrs={"language": "python", "version": "0.1.1"},
                ),
                GraphNode(
                    kind="package",
                    name="beta",
                    path="beta",
                    line=None,
                    attrs={"language": "python", "version": "0.1.1"},
                ),
                GraphNode(kind="app", name="myapp", path="apps/myapp", line=None, attrs={"language": "python"}),
            ],
            edges=[
                # beta (package) depends on alpha
                GraphEdge(
                    src=("package", "beta", "beta"),
                    dst=("package", "alpha", "alpha"),
                    kind="depends_on_package",
                    attrs={},
                ),
                # myapp (app) depends on alpha — describe_package can't surface this
                GraphEdge(
                    src=("app", "myapp", "apps/myapp"),
                    dst=("package", "alpha", "alpha"),
                    kind="depends_on_package",
                    attrs={},
                ),
            ],
        ),
    )
    # Package source.
    assert queries.internal_dependencies_of(conn, name="beta") == ["alpha"]
    # App source works (unlike describe_package, which returns None for apps).
    assert queries.internal_dependencies_of(conn, name="myapp") == ["alpha"]
    # No outgoing edges → empty.
    assert queries.internal_dependencies_of(conn, name="alpha") == []


def test_describe_path(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
                GraphNode(kind="function", name="foo", path="a.py", line=1, attrs={}),
                GraphNode(kind="file", name="b.py", path="b.py", line=None, attrs={}),
            ],
            edges=[
                GraphEdge(src=("file", "a.py", "a.py"), dst=("function", "foo", "a.py"), kind="contains", attrs={}),
                GraphEdge(src=("file", "a.py", "a.py"), dst=("file", "b.py", None), kind="imports", attrs={}),
            ],
        ),
    )
    resolve.sweep(conn)
    desc = queries.describe_path(conn, path="a.py")
    assert desc.path == "a.py"
    assert any(c.name == "foo" for c in desc.children)
    assert any(i.path == "b.py" for i in desc.imports)


def test_describe_package_returns_none_for_missing(conn: sqlite3.Connection) -> None:
    assert queries.describe_package(conn, name="no-such-package") is None


def test_describe_path_returns_none_for_missing(conn: sqlite3.Connection) -> None:
    assert queries.describe_path(conn, path="no/such/path.py") is None


def test_imported_by_returns_importers_with_symbols(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
                GraphNode(kind="file", name="b.py", path="b.py", line=None, attrs={}),
                GraphNode(kind="file", name="c.py", path="c.py", line=None, attrs={}),
                GraphNode(kind="function", name="foo", path="c.py", line=1, attrs={}),
                GraphNode(kind="function", name="bar", path="c.py", line=5, attrs={}),
            ],
            edges=[
                GraphEdge(src=("file", "a.py", "a.py"), dst=("function", "foo", None), kind="imports", attrs={}),
                GraphEdge(src=("file", "a.py", "a.py"), dst=("function", "bar", None), kind="imports", attrs={}),
                GraphEdge(src=("file", "b.py", "b.py"), dst=("function", "foo", None), kind="imports", attrs={}),
            ],
        ),
    )
    resolve.sweep(conn)
    rows = queries.imported_by(conn, path="c.py")
    by_path = {r.path: r for r in rows}
    assert set(by_path) == {"a.py", "b.py"}
    assert by_path["a.py"].symbols == ("bar", "foo")
    assert by_path["b.py"].symbols == ("foo",)
    assert by_path["a.py"].depth == 1


def test_imported_by_symbol_filter_narrows(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
                GraphNode(kind="file", name="b.py", path="b.py", line=None, attrs={}),
                GraphNode(kind="file", name="c.py", path="c.py", line=None, attrs={}),
                GraphNode(kind="function", name="foo", path="c.py", line=1, attrs={}),
                GraphNode(kind="function", name="bar", path="c.py", line=5, attrs={}),
            ],
            edges=[
                GraphEdge(src=("file", "a.py", "a.py"), dst=("function", "foo", None), kind="imports", attrs={}),
                GraphEdge(src=("file", "b.py", "b.py"), dst=("function", "bar", None), kind="imports", attrs={}),
            ],
        ),
    )
    resolve.sweep(conn)
    rows = queries.imported_by(conn, path="c.py", symbol="foo")
    assert [r.path for r in rows] == ["a.py"]
    assert rows[0].symbols == ("foo",)


def test_imported_by_depth_walks_transitively(conn: sqlite3.Connection) -> None:
    # a.py imports b.py; b.py imports c.py — depth=2 from c.py reaches a.py.
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
                GraphNode(kind="file", name="b.py", path="b.py", line=None, attrs={}),
                GraphNode(kind="file", name="c.py", path="c.py", line=None, attrs={}),
            ],
            edges=[
                GraphEdge(src=("file", "a.py", "a.py"), dst=("file", "b.py", None), kind="imports", attrs={}),
                GraphEdge(src=("file", "b.py", "b.py"), dst=("file", "c.py", None), kind="imports", attrs={}),
            ],
        ),
    )
    resolve.sweep(conn)
    direct = queries.imported_by(conn, path="c.py", depth=1)
    assert {r.path for r in direct} == {"b.py"}
    transitive = queries.imported_by(conn, path="c.py", depth=2)
    by_path = {r.path: r for r in transitive}
    assert set(by_path) == {"a.py", "b.py"}
    assert by_path["b.py"].depth == 1
    assert by_path["a.py"].depth == 2


def test_imported_by_excludes_unresolved(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
                GraphNode(kind="file", name="b.py", path="b.py", line=None, attrs={}),
            ],
            edges=[
                GraphEdge(src=("file", "a.py", "a.py"), dst=("file", "b.py", None), kind="imports", attrs={}),
                GraphEdge(src=("file", "a.py", "a.py"), dst=("file", "missing", None), kind="imports", attrs={}),
            ],
        ),
    )
    resolve.sweep(conn)
    rows = queries.imported_by(conn, path="b.py")
    assert [r.path for r in rows] == ["a.py"]


def test_exports_returns_exported_symbols(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
                GraphNode(kind="function", name="foo", path="a.py", line=10, attrs={}),
                GraphNode(kind="function", name="bar", path="a.py", line=20, attrs={}),
            ],
            edges=[
                GraphEdge(src=("file", "a.py", "a.py"), dst=("function", "foo", None), kind="exports", attrs={}),
                GraphEdge(src=("file", "a.py", "a.py"), dst=("function", "bar", None), kind="exports", attrs={}),
            ],
        ),
    )
    resolve.sweep(conn)
    rows = queries.exports(conn, path="a.py")
    by_name = {r.name: r for r in rows}
    assert set(by_name) == {"foo", "bar"}
    assert by_name["foo"].kind == "function"
    assert by_name["foo"].line == 10


def test_exported_by_returns_owning_files(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
                GraphNode(kind="file", name="b.py", path="b.py", line=None, attrs={}),
                GraphNode(kind="function", name="foo", path="a.py", line=1, attrs={}),
                GraphNode(kind="function", name="foo", path="b.py", line=1, attrs={}),
            ],
            edges=[
                GraphEdge(src=("file", "a.py", "a.py"), dst=("function", "foo", None), kind="exports", attrs={}),
                GraphEdge(src=("file", "b.py", "b.py"), dst=("function", "foo", None), kind="exports", attrs={}),
            ],
        ),
    )
    resolve.sweep(conn)
    rows = queries.exported_by(conn, name="foo")
    assert sorted(r.path for r in rows) == ["a.py", "b.py"]
    assert all(r.name == "foo" for r in rows)


# ============================================================================
# dataclass shapes, find() allow-list, fixture audit.
# ============================================================================

import dataclasses  # noqa: E402

from code_graph_io.queries import (  # noqa: E402
    _VALID_KINDS,
    EntryPointDescription,
    PackageDescription,
    PathDescription,
    RepoDescription,
    SuiteDescription,
    find,
)


def test_dataclass_field_shapes() -> None:
    """Every new dataclass has the exact declared field set and is frozen."""
    expected = {
        RepoDescription: {"name", "uri", "owner", "url", "default_branch", "package_count"},
        EntryPointDescription: {
            "name",
            "uri",
            "kind",
            "callable",
            "implemented_by_path",
            "source",
        },
        SuiteDescription: {"name", "uri", "kind", "file_count", "files"},
    }
    for cls, want in expected.items():
        got = {f.name for f in dataclasses.fields(cls)}
        assert got == want, f"{cls.__name__}: expected {want}, got {got}"

    pkg_fields = {f.name for f in dataclasses.fields(PackageDescription)}
    assert {"entry_points", "test_suites"}.issubset(pkg_fields)

    path_fields = {f.name for f in dataclasses.fields(PathDescription)}
    assert "role_flags" in path_fields

    # frozen check
    repo = RepoDescription(
        name="r",
        uri="u",
        owner=None,
        url=None,
        default_branch=None,
        package_count=0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        repo.name = "other"  # type: ignore[misc]


def test_find_unknown_kind_raises(empty_db: sqlite3.Connection) -> None:
    """find raises ValueError when kind is not in _VALID_KINDS."""
    with pytest.raises(ValueError) as exc:
        find(empty_db, name="x", kind="InvalidKind")
    msg = str(exc.value)
    assert "InvalidKind" in msg
    # Allow-list mentioned in message
    for kind in _VALID_KINDS:
        assert kind in msg


def test_find_requires_name_or_kind(empty_db: sqlite3.Connection) -> None:
    """find raises ValueError when neither name nor kind is provided."""
    with pytest.raises(ValueError) as exc:
        find(empty_db)
    msg = str(exc.value).lower()
    assert "name" in msg or "kind" in msg


def _seed_demo_package(conn: sqlite3.Connection) -> None:
    """Seed a single `demo` package containing src/a.py with `def alpha()`."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="package",
                    name="demo",
                    path="demo",
                    line=None,
                    attrs={"language": "python", "version": "0.1.1"},
                ),
                GraphNode(kind="file", name="src/a.py", path="src/a.py", line=None, attrs={}),
                GraphNode(kind="function", name="alpha", path="src/a.py", line=1, attrs={}),
            ],
            edges=[
                GraphEdge(
                    src=("package", "demo", "demo"), dst=("file", "src/a.py", "src/a.py"), kind="contains", attrs={}
                ),
                GraphEdge(
                    src=("file", "src/a.py", "src/a.py"),
                    dst=("function", "alpha", "src/a.py"),
                    kind="contains",
                    attrs={},
                ),
            ],
        ),
    )


def test_find_in_package(conn: sqlite3.Connection) -> None:
    _seed_demo_package(conn)
    rows = queries.find(conn, in_package="demo")
    found = {(r.kind, r.name) for r in rows}
    assert ("file", "src/a.py") in found
    assert ("function", "alpha") in found


def test_find_in_package_case_insensitive(conn: sqlite3.Connection) -> None:
    _seed_demo_package(conn)
    lower = {(r.kind, r.name, r.path) for r in queries.find(conn, in_package="demo")}
    upper = {(r.kind, r.name, r.path) for r in queries.find(conn, in_package="DEMO")}
    mixed = {(r.kind, r.name, r.path) for r in queries.find(conn, in_package="Demo")}
    assert lower == upper == mixed
    assert lower, "expected non-empty result"


def test_find_all_three_filters(conn: sqlite3.Connection) -> None:
    # Two packages each defining a function named "alpha"; AND of all three
    # filters must return only the one inside `demo`.
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="package",
                    name="demo",
                    path="demo",
                    line=None,
                    attrs={"language": "python", "version": "0.1.1"},
                ),
                GraphNode(
                    kind="package",
                    name="other",
                    path="other",
                    line=None,
                    attrs={"language": "python", "version": "0.1.1"},
                ),
                GraphNode(kind="file", name="demo/a.py", path="demo/a.py", line=None, attrs={}),
                GraphNode(kind="file", name="other/a.py", path="other/a.py", line=None, attrs={}),
                GraphNode(kind="function", name="alpha", path="demo/a.py", line=1, attrs={}),
                GraphNode(kind="function", name="alpha", path="other/a.py", line=1, attrs={}),
            ],
            edges=[
                GraphEdge(
                    src=("package", "demo", "demo"), dst=("file", "demo/a.py", "demo/a.py"), kind="contains", attrs={}
                ),
                GraphEdge(
                    src=("package", "other", "other"),
                    dst=("file", "other/a.py", "other/a.py"),
                    kind="contains",
                    attrs={},
                ),
            ],
        ),
    )
    rows = queries.find(conn, name="alpha", kind="function", in_package="demo")
    assert [(r.kind, r.name, r.path) for r in rows] == [("function", "alpha", "demo/a.py")]


def test_find_no_filters_raises(empty_db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError) as exc:
        find(empty_db)
    msg = str(exc.value).lower()
    assert "name" in msg
    assert "kind" in msg
    # accept both `in_package` and `in-package` spellings
    assert "in_package" in msg or "in-package" in msg


def _count(conn: sqlite3.Connection, sql: str, *params) -> int:
    return conn.execute(sql, params).fetchone()[0]


def test_ambient_workspace_env_is_isolated() -> None:
    """Regression: the ambient GRAPH_WIKI_WORKSPACE must be stripped for the
    code-graph-io session so fixtures never resolve against — and clobber — a real
    workspace's manifest/code.db. See conftest._isolate_from_ambient_workspace.
    """
    import os

    assert os.environ.get("GRAPH_WIKI_WORKSPACE") is None


def test_seeded_db_fixture_audit(seeded_db: sqlite3.Connection) -> None:
    """sample_monorepo fixture has the expected shape."""
    missing: list[str] = []

    n_ep_callable = _count(
        seeded_db,
        "SELECT COUNT(*) FROM nodes WHERE kind='entry_point' AND json_extract(attrs_json, '$.callable') IS NOT NULL",
    )
    if n_ep_callable < 1:
        missing.append("need >= 1 EntryPoint with non-null callable")

    n_ep_wildcard = _count(
        seeded_db,
        "SELECT COUNT(*) FROM nodes WHERE kind='entry_point' AND json_extract(attrs_json, '$.is_wildcard') = 1",
    )
    if n_ep_wildcard < 1:
        missing.append("need >= 1 wildcard EntryPoint (jspkg/package.json exports with '*')")

    assert not missing, "sample_monorepo fixture is missing required items:\n" + "\n".join(f"  - {m}" for m in missing)


# ============================================================================
# find per-kind, describe_*, list_*, extended describe_*.
# ============================================================================

from code_graph_io.queries import (  # noqa: E402
    describe_entry_point,
    describe_package,
    describe_path,
    describe_repository,
    describe_test_suite,
    list_entry_points,
    list_packages,
    list_repositories,
    list_scripts,
    list_test_suites,
)


@pytest.mark.parametrize(
    "kind",
    [
        "function",
        "class",
        "method",
        "file",
        "package",
        "repository",
        "subpackage",
        "entry_point",
        "test_suite",
    ],
)
def test_find_per_kind(seeded_db: sqlite3.Connection, kind: str) -> None:
    rows = find(seeded_db, kind=kind)
    assert isinstance(rows, list)
    if kind in {"file", "package", "repository"}:
        assert rows, f"expected non-empty result for kind={kind!r}"
    for r in rows:
        assert r.kind == kind


def test_describe_repository(seeded_db: sqlite3.Connection) -> None:
    repo = describe_repository(seeded_db)
    assert repo is not None
    assert isinstance(repo, RepoDescription)
    assert repo.name
    assert repo.package_count >= 1


def test_describe_repository_returns_none_on_empty_db(
    empty_db: sqlite3.Connection,
) -> None:
    assert describe_repository(empty_db) is None


def test_describe_entry_point(seeded_db: sqlite3.Connection) -> None:
    eps = list_entry_points(seeded_db)
    if not eps:
        pytest.skip("seeded_db has no EntryPoint nodes")
    first = eps[0]
    pkg_row = seeded_db.execute(
        "SELECT p.name FROM edges e "
        "JOIN nodes p ON e.src = p.id "
        "WHERE e.kind='declares_entry_point' AND e.dst = ("
        "  SELECT id FROM nodes WHERE kind='entry_point' AND name = ?"
        ") LIMIT 1",
        (first.name,),
    ).fetchone()
    assert pkg_row is not None
    ep = describe_entry_point(
        seeded_db,
        package_name=pkg_row[0],
        entry_name=first.name,
    )
    assert ep is not None
    assert isinstance(ep, EntryPointDescription)
    assert ep.name == first.name
    assert ep.kind in {"executable", "library"}


def test_describe_entry_point_returns_none_on_missing(
    empty_db: sqlite3.Connection,
) -> None:
    assert describe_entry_point(empty_db, package_name="x", entry_name="y") is None


def test_describe_test_suite(seeded_db: sqlite3.Connection) -> None:
    suites = list_test_suites(seeded_db)
    if not suites:
        pytest.skip("seeded_db has no TestSuite nodes")
    first = suites[0]
    s = describe_test_suite(seeded_db, suite_name=first.name)
    assert s is not None
    assert isinstance(s, SuiteDescription)
    assert s.name == first.name


def test_describe_test_suite_includes_files(seeded_db: sqlite3.Connection) -> None:
    """`SuiteDescription.files` carries the same paths `file_count` counts."""
    s = describe_test_suite(seeded_db, suite_name="mypkg-unit-tests")
    assert s is not None
    assert s.files == ["packages/mypkg/tests/test_foo.py"]
    assert s.file_count == len(s.files)


def test_describe_test_suite_returns_none_on_missing(
    empty_db: sqlite3.Connection,
) -> None:
    assert describe_test_suite(empty_db, suite_name="x") is None


@pytest.mark.parametrize(
    "fn, kind",
    [
        (list_repositories, "repository"),
        (list_packages, "package"),
        (list_entry_points, "entry_point"),
        (list_test_suites, "test_suite"),
    ],
)
def test_list_returns_sorted_node_records(seeded_db: sqlite3.Connection, fn, kind: str) -> None:
    rows = fn(seeded_db)
    assert isinstance(rows, list)
    assert all(r.kind == kind for r in rows)
    names = [r.name for r in rows]
    assert names == sorted(names), f"{fn.__name__} not alphabetical"


def test_list_returns_empty_on_empty_db(empty_db: sqlite3.Connection) -> None:
    for fn in [
        list_repositories,
        list_packages,
        list_entry_points,
        list_test_suites,
        list_scripts,
    ]:
        assert fn(empty_db) == []


def test_list_scripts(seeded_db: sqlite3.Connection) -> None:
    scripts = list_scripts(seeded_db)
    assert isinstance(scripts, list)
    for r in scripts:
        assert r.kind in {"file", "entry_point"}
        if r.kind == "file":
            assert r.attrs.get("is_executable") is True
        else:
            assert r.attrs.get("entry_kind") == "executable"


def test_describe_package_extended(seeded_db: sqlite3.Connection) -> None:
    pkgs = list_packages(seeded_db)
    assert pkgs, "expected packages in seeded_db"
    name = pkgs[0].name
    desc = describe_package(seeded_db, name=name)
    assert desc is not None
    assert isinstance(desc.entry_points, list)
    assert isinstance(desc.test_suites, list)
    for ep in desc.entry_points:
        assert isinstance(ep, EntryPointDescription)
    for ts in desc.test_suites:
        assert isinstance(ts, SuiteDescription)


def test_describe_package_returns_none_on_missing(
    empty_db: sqlite3.Connection,
) -> None:
    assert describe_package(empty_db, name="__nonexistent__") is None


def test_describe_path_role_flags(seeded_db: sqlite3.Connection) -> None:
    row = seeded_db.execute("SELECT path FROM nodes WHERE kind='file' LIMIT 1").fetchone()
    assert row is not None, "expected a File node"
    desc = describe_path(seeded_db, path=row[0])
    assert desc is not None
    assert isinstance(desc.role_flags, dict)
    assert set(desc.role_flags.keys()) == {
        "is_importable",
        "has_main",
        "is_test",
        "is_config",
        "is_generated",
        "is_type_only",
        "is_executable",
    }
    for k, v in desc.role_flags.items():
        assert isinstance(v, bool), f"{k}: {v!r} not a bool"


def test_describe_path_returns_none_on_missing_empty_db(
    empty_db: sqlite3.Connection,
) -> None:
    assert describe_path(empty_db, path="nonexistent/path.py") is None


# ============================================================================
# bubble-up + cross-cutting + CTE cycle-safety tests.
# ============================================================================

from code_graph_io.queries import (  # noqa: E402
    entry_points_for_package,
    tests_for_package,
)

# pytest's default `python_functions` rule collects any callable whose name
# starts with "test" — `tests_for_package` happens to match. Mark it as a
# non-test callable so pytest's collector skips it (the actual unit tests
# below have the explicit `test_` prefix).
tests_for_package.__test__ = False  # type: ignore[attr-defined]


# --- happy-path tests against seeded_db ------------------------------------


def test_tests_for_package(seeded_db: sqlite3.Connection) -> None:
    pkgs = list_packages(seeded_db)
    assert pkgs
    for pkg in pkgs:
        suites = tests_for_package(seeded_db, package_name=pkg.name)
        if suites:
            for s in suites:
                assert isinstance(s, SuiteDescription)
            return
    pytest.skip("seeded_db has no package with tests edges")


def test_entry_points_for_package(seeded_db: sqlite3.Connection) -> None:
    pkgs = list_packages(seeded_db)
    assert pkgs
    for pkg in pkgs:
        eps = entry_points_for_package(seeded_db, package_name=pkg.name)
        if eps:
            for e in eps:
                assert isinstance(e, EntryPointDescription)
            names = [e.name for e in eps]
            assert names == sorted(names)
            return
    pytest.skip("seeded_db has no package with EntryPoints")


# --- empty-DB graceful degradation -----------------------------------------


def test_tests_for_package_returns_empty_on_empty_db(
    empty_db: sqlite3.Connection,
) -> None:
    assert tests_for_package(empty_db, package_name="x") == []


def test_entry_points_for_package_returns_empty_on_empty_db(
    empty_db: sqlite3.Connection,
) -> None:
    assert entry_points_for_package(empty_db, package_name="x") == []


# ============================================================================
# dependency + plugin admitted kinds, dataclasses, helpers.
# ============================================================================


def test_valid_kinds_includes_dependency_and_agent_plugin(conn: sqlite3.Connection) -> None:
    """_VALID_KINDS carries dependency + agent_plugin; legacy plugin is gone."""
    assert "dependency" in queries._VALID_KINDS
    assert "agent_plugin" in queries._VALID_KINDS
    assert "plugin" not in queries._VALID_KINDS
    rows = queries.find(conn, kind="agent_plugin")
    assert rows == []


def test_valid_kinds_includes_builtin() -> None:
    """_VALID_KINDS admits the builtin kind."""
    assert "builtin" in queries._VALID_KINDS


def test_valid_kinds_includes_app() -> None:
    """_VALID_KINDS admits the app kind."""
    assert "app" in queries._VALID_KINDS


def test_valid_kinds_includes_type(conn: sqlite3.Connection) -> None:
    """_VALID_KINDS admits the type kind; find(kind='type') does not raise."""
    assert "type" in queries._VALID_KINDS
    rows = queries.find(conn, kind="type")
    assert rows == []


def test_valid_kinds_includes_unresolved_symbol(conn: sqlite3.Connection) -> None:
    """find() admits explicit unresolved call/export target placeholders."""
    assert "unresolved_symbol" in queries._VALID_KINDS
    rows = queries.find(conn, kind="unresolved_symbol")
    assert rows == []


def test_valid_app_kinds_contents() -> None:
    """_VALID_APP_KINDS frozenset enumerates the framework strings."""
    assert frozenset({"cli", "electron", "expo", "nextjs", "server", "spa"}) == queries._VALID_APP_KINDS


def test_builtin_uri_shape() -> None:
    """builtin_uri returns builtin:<language>/<module_name>."""
    from code_graph_io.uri import builtin_uri

    assert builtin_uri("python", "pathlib") == "builtin:python/pathlib"
    assert builtin_uri("javascript", "fs") == "builtin:javascript/fs"
    assert builtin_uri("python", "os.path") == "builtin:python/os.path"


def test_describe_dependency_returns_dependency_description(conn: sqlite3.Connection) -> None:
    """describe_dependency populates from node attrs + inbound used_by edges."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="dependency",
                    name="boto3",
                    path="dependency:local/repo:pypi:boto3",
                    line=None,
                    attrs={
                        "ecosystem": "pypi",
                        "name": "boto3",
                        "uri": "dependency:local/repo/pypi/boto3",
                        "versions_in_use": ["boto3>=1.38", "boto3==1.39.0"],
                    },
                ),
                GraphNode(
                    kind="package",
                    name="my-pkg",
                    path="src/my_pkg",
                    line=None,
                    attrs={"uri": "pkg:local/repo/my-pkg"},
                ),
            ],
            edges=[
                GraphEdge(
                    src=("package", "my-pkg", "src/my_pkg"),
                    dst=("dependency", "boto3", "dependency:local/repo:pypi:boto3"),
                    kind="used_by",
                    attrs={},
                ),
            ],
        ),
    )
    d = queries.describe_dependency(conn, uri="dependency:local/repo/pypi/boto3")
    assert d is not None
    assert d.ecosystem == "pypi"
    assert d.name == "boto3"
    assert d.uri == "dependency:local/repo/pypi/boto3"
    assert d.versions_in_use == ["boto3>=1.38", "boto3==1.39.0"]
    assert d.used_by == ["pkg:local/repo/my-pkg"]
    assert d.implemented_by == []
    assert d.ambiguous is False


def test_describe_package_used_by_and_versions_in_use_match_describe_dependency(
    conn: sqlite3.Connection,
) -> None:
    """A workspace-implemented dependency's `used_by`/`versions_in_use` are also
    reachable from the implementing Package, and agree with `describe_dependency`
    by construction (same consumer-kind filter, same ordering)."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="dependency",
                    name="shared-dep",
                    path="dependency:local/repo:pypi:shared-dep",
                    line=None,
                    attrs={
                        "ecosystem": "pypi",
                        "name": "shared-dep",
                        "uri": "dependency:local/repo/pypi/shared-dep",
                        "versions_in_use": ["shared-dep>=1.0"],
                    },
                ),
                GraphNode(
                    kind="package",
                    name="impl-pkg",
                    path="src/impl_pkg",
                    line=None,
                    attrs={"uri": "pkg:local/repo/impl-pkg"},
                ),
                GraphNode(
                    kind="package",
                    name="consumer-pkg",
                    path="src/consumer_pkg",
                    line=None,
                    attrs={"uri": "pkg:local/repo/consumer-pkg"},
                ),
                GraphNode(
                    kind="repository",
                    name="root-repo",
                    path="",
                    line=None,
                    attrs={"owner": "o", "name": "root-repo", "uri": "repo:o/root-repo"},
                ),
            ],
            edges=[
                GraphEdge(
                    src=("dependency", "shared-dep", "dependency:local/repo:pypi:shared-dep"),
                    dst=("package", "impl-pkg", "src/impl_pkg"),
                    kind="implemented_by",
                    attrs={},
                ),
                GraphEdge(
                    src=("package", "consumer-pkg", "src/consumer_pkg"),
                    dst=("dependency", "shared-dep", "dependency:local/repo:pypi:shared-dep"),
                    kind="used_by",
                    attrs={},
                ),
                GraphEdge(
                    src=("repository", "root-repo", ""),
                    dst=("dependency", "shared-dep", "dependency:local/repo:pypi:shared-dep"),
                    kind="used_by",
                    attrs={"dev": True},
                ),
            ],
        ),
    )
    d = queries.describe_dependency(conn, uri="dependency:local/repo/pypi/shared-dep")
    assert d is not None
    p = queries.describe_package(conn, name="impl-pkg")
    assert p is not None
    assert p.used_by == d.used_by
    assert p.versions_in_use == d.versions_in_use
    assert p.used_by == ["pkg:local/repo/consumer-pkg", "repo:o/root-repo"]
    assert p.versions_in_use == ["shared-dep>=1.0"]


def test_describe_package_used_by_and_versions_in_use_empty_when_not_implemented(
    conn: sqlite3.Connection,
) -> None:
    """A Package with no implemented_by edge (not the workspace implementation of
    any dependency) gets empty lists, not an error."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="package",
                    name="lonely-pkg",
                    path="src/lonely_pkg",
                    line=None,
                    attrs={"uri": "pkg:local/repo/lonely-pkg"},
                ),
            ],
            edges=[],
        ),
    )
    p = queries.describe_package(conn, name="lonely-pkg")
    assert p is not None
    assert p.used_by == []
    assert p.versions_in_use == []


def test_describe_dependency_retains_sorted_deduplicated_implementations(conn: sqlite3.Connection) -> None:
    """The dependency read model exposes every distinct Package URI in URI order."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="dependency",
                    name="shared",
                    path="dependency:local/repo:pypi:shared",
                    line=None,
                    attrs={"ecosystem": "pypi", "name": "shared", "uri": "dependency:local/repo/pypi/shared"},
                ),
                GraphNode(
                    kind="package",
                    name="second",
                    path="packages/second",
                    line=None,
                    attrs={"uri": "pkg:acme/two/shared"},
                ),
                GraphNode(
                    kind="package",
                    name="first",
                    path="packages/first",
                    line=None,
                    attrs={"uri": "pkg:acme/one/shared"},
                ),
                GraphNode(
                    kind="package",
                    name="duplicate-first",
                    path="packages/duplicate-first",
                    line=None,
                    attrs={"uri": "pkg:acme/one/shared"},
                ),
            ],
            edges=[
                GraphEdge(
                    src=("dependency", "shared", "dependency:local/repo:pypi:shared"),
                    dst=("package", "second", "packages/second"),
                    kind="implemented_by",
                    attrs={},
                ),
                GraphEdge(
                    src=("dependency", "shared", "dependency:local/repo:pypi:shared"),
                    dst=("package", "first", "packages/first"),
                    kind="implemented_by",
                    attrs={},
                ),
                GraphEdge(
                    src=("dependency", "shared", "dependency:local/repo:pypi:shared"),
                    dst=("package", "duplicate-first", "packages/duplicate-first"),
                    kind="implemented_by",
                    attrs={},
                ),
            ],
        ),
    )

    description = queries.describe_dependency(conn, uri="dependency:local/repo/pypi/shared")

    assert description is not None
    assert description.implemented_by == ["pkg:acme/one/shared", "pkg:acme/two/shared"]
    assert description.ambiguous is True


def test_describe_dependency_with_one_implementation_is_not_ambiguous(conn: sqlite3.Connection) -> None:
    """One implementation remains a resolvable dependency."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="dependency",
                    name="solo",
                    path="dependency:local/repo:pypi:solo",
                    line=None,
                    attrs={"ecosystem": "pypi", "name": "solo", "uri": "dependency:local/repo/pypi/solo"},
                ),
                GraphNode(
                    kind="package",
                    name="solo-package",
                    path="packages/solo",
                    line=None,
                    attrs={"uri": "pkg:acme/one/solo"},
                ),
            ],
            edges=[
                GraphEdge(
                    src=("dependency", "solo", "dependency:local/repo:pypi:solo"),
                    dst=("package", "solo-package", "packages/solo"),
                    kind="implemented_by",
                    attrs={},
                ),
            ],
        ),
    )

    description = queries.describe_dependency(conn, uri="dependency:local/repo/pypi/solo")

    assert description is not None
    assert description.implemented_by == ["pkg:acme/one/solo"]
    assert description.ambiguous is False


def test_describe_dependency_returns_none_when_missing(conn: sqlite3.Connection) -> None:
    assert queries.describe_dependency(conn, uri="dependency:local/repo/pypi/nonexistent") is None


def test_describe_dependency_includes_app_only_consumer(conn: sqlite3.Connection) -> None:
    """A dependency consumed exclusively by an App still renders a non-empty used_by."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="dependency",
                    name="typer",
                    path="dependency:local/repo:pypi:typer",
                    line=None,
                    attrs={"ecosystem": "pypi", "name": "typer", "uri": "dependency:local/repo/pypi/typer"},
                ),
                GraphNode(
                    kind="app",
                    name="work-tracker-okf",
                    path="apps/work-tracker-okf",
                    line=None,
                    attrs={
                        "language": "python",
                        "uri": "app:o/r/work-tracker-okf",
                        "app_kind": "cli",
                        "app_signals": [],
                    },
                ),
            ],
            edges=[
                GraphEdge(
                    src=("app", "work-tracker-okf", "apps/work-tracker-okf"),
                    dst=("dependency", "typer", "dependency:local/repo:pypi:typer"),
                    kind="used_by",
                    attrs={},
                ),
            ],
        ),
    )
    d = queries.describe_dependency(conn, uri="dependency:local/repo/pypi/typer")
    assert d is not None
    assert d.used_by == ["app:o/r/work-tracker-okf"]


def test_describe_dependency_includes_repository_consumer(conn: sqlite3.Connection) -> None:
    """A dependency re-sourced to the Repository (a virtual workspace root's
    dev tooling) still renders a non-empty used_by."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="dependency",
                    name="mypy",
                    path="dependency:local/repo:pypi:mypy",
                    line=None,
                    attrs={"ecosystem": "pypi", "name": "mypy", "uri": "dependency:local/repo/pypi/mypy"},
                ),
                GraphNode(
                    kind="repository",
                    name="agent-workspace",
                    path="",
                    line=None,
                    attrs={"owner": "o", "name": "agent-workspace", "uri": "repo:o/agent-workspace"},
                ),
            ],
            edges=[
                GraphEdge(
                    src=("repository", "agent-workspace", ""),
                    dst=("dependency", "mypy", "dependency:local/repo:pypi:mypy"),
                    kind="used_by",
                    attrs={"dev": True},
                ),
            ],
        ),
    )
    d = queries.describe_dependency(conn, uri="dependency:local/repo/pypi/mypy")
    assert d is not None
    assert d.used_by == ["repo:o/agent-workspace"]


def test_describe_dependency_used_by_matches_consumer_packages(conn: sqlite3.Connection) -> None:
    """For a mixed package+app consumer set, describe_dependency agrees with consumer_packages."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="dependency",
                    name="typer",
                    path="dependency:local/repo:pypi:typer",
                    line=None,
                    attrs={"ecosystem": "pypi", "name": "typer", "uri": "dependency:local/repo/pypi/typer"},
                ),
                GraphNode(
                    kind="package",
                    name="my-pkg",
                    path="src/my_pkg",
                    line=None,
                    attrs={"uri": "pkg:local/repo/my-pkg"},
                ),
                GraphNode(
                    kind="app",
                    name="my-app",
                    path="apps/my-app",
                    line=None,
                    attrs={"language": "python", "uri": "app:o/r/my-app", "app_kind": "cli", "app_signals": []},
                ),
            ],
            edges=[
                GraphEdge(
                    src=("package", "my-pkg", "src/my_pkg"),
                    dst=("dependency", "typer", "dependency:local/repo:pypi:typer"),
                    kind="used_by",
                    attrs={},
                ),
                GraphEdge(
                    src=("app", "my-app", "apps/my-app"),
                    dst=("dependency", "typer", "dependency:local/repo:pypi:typer"),
                    kind="used_by",
                    attrs={},
                ),
            ],
        ),
    )
    d = queries.describe_dependency(conn, uri="dependency:local/repo/pypi/typer")
    assert d is not None
    cp = queries.consumer_packages(conn, kind="dependency", entity_uri="dependency:local/repo/pypi/typer")
    # Same consumer *set*, two representations: describe_dependency carries
    # page-resolvable URIs (ADR-0048), consumer_packages stays the
    # domain-agnostic name query it documents itself as. The guard is that
    # neither silently gains or loses a consumer relative to the other.
    assert [uri.rsplit("/", 1)[-1] for uri in d.used_by] == list(cp)


def test_describe_dependency_separates_same_named_package_and_app(conn: sqlite3.Connection) -> None:
    """A package and an app sharing a name stay two used_by entries.

    Under the old bare-name representation these collapsed to one, which said
    "used by twin" where two distinct entities — with two distinct pages —
    each use the dependency. URIs (ADR-0048) keep them apart and let each
    resolve to its own page."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="dependency",
                    name="typer",
                    path="dependency:local/repo:pypi:typer",
                    line=None,
                    attrs={"ecosystem": "pypi", "name": "typer", "uri": "dependency:local/repo/pypi/typer"},
                ),
                GraphNode(
                    kind="package",
                    name="twin",
                    path="src/twin",
                    line=None,
                    attrs={"uri": "pkg:local/repo/twin"},
                ),
                GraphNode(
                    kind="app",
                    name="twin",
                    path="apps/twin",
                    line=None,
                    attrs={"language": "python", "uri": "app:o/r/twin", "app_kind": "cli", "app_signals": []},
                ),
            ],
            edges=[
                GraphEdge(
                    src=("package", "twin", "src/twin"),
                    dst=("dependency", "typer", "dependency:local/repo:pypi:typer"),
                    kind="used_by",
                    attrs={},
                ),
                GraphEdge(
                    src=("app", "twin", "apps/twin"),
                    dst=("dependency", "typer", "dependency:local/repo:pypi:typer"),
                    kind="used_by",
                    attrs={},
                ),
            ],
        ),
    )
    d = queries.describe_dependency(conn, uri="dependency:local/repo/pypi/typer")
    assert d is not None
    assert d.used_by == ["app:o/r/twin", "pkg:local/repo/twin"]


def test_describe_agent_plugin_returns_description(conn: sqlite3.Connection) -> None:
    """describe_agent_plugin returns AgentPluginDescription from node attrs."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="agent_plugin",
                    name="agent-workspace",
                    path=None,
                    line=None,
                    attrs={
                        "uri": "agent_plugin:test/repo/agent-workspace",
                        "ecosystem": "claude-code",
                        "version": "0.1.1",
                        "description": "A wiki plugin.",
                        "components": {
                            "commands": [
                                {
                                    "id": "command:test/repo/agent-workspace/scan",
                                    "name": "scan",
                                    "description": "Walk the monorepo.",
                                }
                            ],
                            "agents": [],
                            "skills": [],
                            "scripts": [],
                            "hooks": [],
                            "mcp_servers": [],
                        },
                    },
                ),
            ],
            edges=[],
        ),
    )
    p = queries.describe_agent_plugin(conn, name="agent-workspace")
    assert p is not None
    assert p.name == "agent-workspace"
    assert p.uri == "agent_plugin:test/repo/agent-workspace"
    assert p.ecosystem == "claude-code"
    assert p.version == "0.1.1"
    assert p.description == "A wiki plugin."
    assert p.commands == [
        {"id": "command:test/repo/agent-workspace/scan", "name": "scan", "description": "Walk the monorepo."}
    ]
    assert p.agents == [] and p.mcp_servers == []
    assert p.package_name is None


def test_describe_agent_plugin_returns_none_when_missing(conn: sqlite3.Connection) -> None:
    assert queries.describe_agent_plugin(conn, name="nonexistent") is None


def test_describe_agent_plugin_reports_sibling_package_name_via_facet_of(conn: sqlite3.Connection) -> None:
    """A plugin root that also carries a package manifest is linked
    `Package --facet_of--> agent_plugin` (facet model) — `describe_agent_plugin`
    must surface the sibling Package's name as `package_name`."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="package",
                    name="graph-works",
                    path="plugins/graph-works",
                    line=None,
                    attrs={"uri": "pkg:local/repo/graph-works", "language": "python", "version": "0.1.0"},
                ),
                GraphNode(
                    kind="agent_plugin",
                    name="graph-works",
                    path="plugins/graph-works",
                    line=None,
                    attrs={
                        "uri": "agent_plugin:test/repo/graph-works",
                        "ecosystem": "claude-code",
                        "version": "0.1.0",
                        "description": "An epic plugin.",
                        "components": {},
                    },
                ),
            ],
            edges=[
                GraphEdge(
                    src=("package", "graph-works", "plugins/graph-works"),
                    dst=("agent_plugin", "graph-works", "plugins/graph-works"),
                    kind="facet_of",
                    attrs={},
                ),
            ],
        ),
    )
    p = queries.describe_agent_plugin(conn, name="graph-works")
    assert p is not None
    assert p.package_name == "graph-works"


def test_list_dependencies_alphabetical(conn: sqlite3.Connection) -> None:
    """list_dependencies returns alphabetically-sorted dependency NodeRecords."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="dependency",
                    name="zlib",
                    path=None,
                    line=None,
                    attrs={"ecosystem": "pypi", "uri": "dependency:pypi/zlib"},
                ),
                GraphNode(
                    kind="dependency",
                    name="boto3",
                    path=None,
                    line=None,
                    attrs={"ecosystem": "pypi", "uri": "dependency:pypi/boto3"},
                ),
                GraphNode(
                    kind="dependency",
                    name="langchain-aws",
                    path=None,
                    line=None,
                    attrs={"ecosystem": "pypi", "uri": "dependency:pypi/langchain-aws"},
                ),
            ],
            edges=[],
        ),
    )
    assert [n.name for n in queries.list_dependencies(conn)] == ["boto3", "langchain-aws", "zlib"]


def test_list_builtins_alphabetical(conn: sqlite3.Connection) -> None:
    """list_builtins returns alphabetically-sorted Builtin NodeRecords."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="builtin",
                    name="sys",
                    path="python",
                    line=None,
                    attrs={"language": "python", "module_name": "sys", "uri": "builtin:python/sys"},
                ),
                GraphNode(
                    kind="builtin",
                    name="os",
                    path="python",
                    line=None,
                    attrs={"language": "python", "module_name": "os", "uri": "builtin:python/os"},
                ),
                GraphNode(
                    kind="builtin",
                    name="pathlib",
                    path="python",
                    line=None,
                    attrs={"language": "python", "module_name": "pathlib", "uri": "builtin:python/pathlib"},
                ),
            ],
            edges=[],
        ),
    )
    assert [n.name for n in queries.list_builtins(conn)] == ["os", "pathlib", "sys"]


def test_describe_builtin_returns_description(conn: sqlite3.Connection) -> None:
    """describe_builtin returns populated BuiltinDescription."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="builtin",
                    name="pathlib",
                    path="python",
                    line=None,
                    attrs={
                        "language": "python",
                        "module_name": "pathlib",
                        "uri": "builtin:python/pathlib",
                    },
                ),
                GraphNode(
                    kind="package",
                    name="demo",
                    path="src/demo",
                    line=None,
                    attrs={"uri": "pkg:local/repo/demo"},
                ),
            ],
            edges=[
                GraphEdge(
                    src=("package", "demo", "src/demo"),
                    dst=("builtin", "pathlib", "python"),
                    kind="used_by",
                    attrs={},
                ),
            ],
        ),
    )
    b = queries.describe_builtin(conn, language="python", module_name="pathlib")
    assert b is not None
    assert b.language == "python"
    assert b.module_name == "pathlib"
    assert b.uri == "builtin:python/pathlib"
    assert b.used_by == ["demo"]


def test_describe_builtin_returns_none_when_missing(conn: sqlite3.Connection) -> None:
    assert queries.describe_builtin(conn, language="python", module_name="nonexistent") is None


def test_describe_builtin_dedupes_same_named_package_and_app(conn: sqlite3.Connection) -> None:
    """A package and an app sharing a name collapse to one used_by entry."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="builtin",
                    name="pathlib",
                    path="python",
                    line=None,
                    attrs={"language": "python", "module_name": "pathlib", "uri": "builtin:python/pathlib"},
                ),
                GraphNode(
                    kind="package",
                    name="twin",
                    path="src/twin",
                    line=None,
                    attrs={"uri": "pkg:local/repo/twin"},
                ),
                GraphNode(
                    kind="app",
                    name="twin",
                    path="apps/twin",
                    line=None,
                    attrs={"language": "python", "uri": "app:o/r/twin", "app_kind": "cli", "app_signals": []},
                ),
            ],
            edges=[
                GraphEdge(
                    src=("package", "twin", "src/twin"),
                    dst=("builtin", "pathlib", "python"),
                    kind="used_by",
                    attrs={},
                ),
                GraphEdge(
                    src=("app", "twin", "apps/twin"),
                    dst=("builtin", "pathlib", "python"),
                    kind="used_by",
                    attrs={},
                ),
            ],
        ),
    )
    b = queries.describe_builtin(conn, language="python", module_name="pathlib")
    assert b is not None
    assert b.used_by == ["twin"]


def test_describe_builtin_includes_app_only_consumer(conn: sqlite3.Connection) -> None:
    """A builtin consumed exclusively by an App still renders a non-empty used_by."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="builtin",
                    name="pathlib",
                    path="python",
                    line=None,
                    attrs={"language": "python", "module_name": "pathlib", "uri": "builtin:python/pathlib"},
                ),
                GraphNode(
                    kind="app",
                    name="work-tracker-okf",
                    path="apps/work-tracker-okf",
                    line=None,
                    attrs={
                        "language": "python",
                        "uri": "app:o/r/work-tracker-okf",
                        "app_kind": "cli",
                        "app_signals": [],
                    },
                ),
            ],
            edges=[
                GraphEdge(
                    src=("app", "work-tracker-okf", "apps/work-tracker-okf"),
                    dst=("builtin", "pathlib", "python"),
                    kind="used_by",
                    attrs={},
                ),
            ],
        ),
    )
    b = queries.describe_builtin(conn, language="python", module_name="pathlib")
    assert b is not None
    assert b.used_by == ["work-tracker-okf"]


def test_describe_builtin_filters_by_language(conn: sqlite3.Connection) -> None:
    """describe_builtin filters by language — same module_name, different language.
    path=language is used as the upsert key discriminator so both can coexist in the DB.
    """
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="builtin",
                    name="os",
                    path="python",
                    line=None,
                    attrs={"language": "python", "module_name": "os", "uri": "builtin:python/os"},
                ),
                GraphNode(
                    kind="builtin",
                    name="os",
                    path="javascript",
                    line=None,
                    attrs={"language": "javascript", "module_name": "os", "uri": "builtin:javascript/os"},
                ),
            ],
            edges=[],
        ),
    )
    result = queries.describe_builtin(conn, language="python", module_name="os")
    assert result is not None
    assert result.language == "python"
    assert result.uri == "builtin:python/os"


# ============================================================================
# AppDescription, list_apps, describe_app.
# ============================================================================


def test_list_apps_alphabetical(conn: sqlite3.Connection) -> None:
    """list_apps returns alphabetically-sorted App NodeRecords."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="app",
                    name="zeta-cli",
                    path="apps/zeta",
                    line=None,
                    attrs={"language": "python", "uri": "app:o/r/zeta-cli", "app_kind": "cli", "app_signals": ["cli"]},
                ),
                GraphNode(
                    kind="app",
                    name="alpha-cli",
                    path="apps/alpha",
                    line=None,
                    attrs={"language": "python", "uri": "app:o/r/alpha-cli", "app_kind": "cli", "app_signals": ["cli"]},
                ),
                GraphNode(
                    kind="app",
                    name="middle-app",
                    path="apps/middle",
                    line=None,
                    attrs={
                        "language": "javascript",
                        "uri": "app:o/r/middle-app",
                        "app_kind": "nextjs",
                        "app_signals": ["cli", "nextjs"],
                    },
                ),
            ],
            edges=[],
        ),
    )
    assert [n.name for n in queries.list_apps(conn)] == [
        "alpha-cli",
        "middle-app",
        "zeta-cli",
    ]


def test_describe_app_returns_app_description(conn: sqlite3.Connection) -> None:
    """describe_app returns AppDescription with all fields populated."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="app",
                    name="my-cli",
                    path="apps/my-cli",
                    line=None,
                    attrs={
                        "language": "python",
                        "version": "0.1.1",
                        "uri": "app:o/r/my-cli",
                        "app_kind": "cli",
                        "app_signals": ["cli"],
                    },
                ),
            ],
            edges=[],
        ),
    )
    desc = queries.describe_app(conn, name="my-cli")
    assert desc is not None
    assert desc.name == "my-cli"
    assert desc.language == "python"
    assert desc.version == "0.1.1"
    assert desc.app_kind == "cli"
    assert desc.app_signals == ["cli"]
    assert desc.files == []
    assert desc.counts == {}
    assert desc.entry_points == []
    assert desc.test_suites == []


def test_describe_app_returns_none_when_missing(conn: sqlite3.Connection) -> None:
    """describe_app returns None for an unknown app name."""
    assert queries.describe_app(conn, name="nonexistent-app") is None


def test_describe_app_does_not_match_package_kind(conn: sqlite3.Connection) -> None:
    """describe_app's filter is strictly kind='app' — a name that
    only exists under kind='package' returns None."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="package",
                    name="shared-name",
                    path="pkgs/shared-name",
                    line=None,
                    attrs={"uri": "pkg:o/r/shared-name"},
                ),
            ],
            edges=[],
        ),
    )
    # No kind='app' row with this name → describe_app returns None even though
    # a kind='package' row exists.
    assert queries.describe_app(conn, name="shared-name") is None


def test_describe_app_sources_files_and_entry_points_from_sibling_package(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """Facet model: files/entry_points/test_suites all source from the App's
    sibling Package node of the same name — describe_app must resolve to the
    same content describe_package would, since contains/declares_entry_point
    edges only ever originate from the Package node. app_kind/app_signals
    still come from the App node's own attrs."""
    from code_graph_io import entry_points, packages
    from code_graph_io.uri import RepoContext

    pkg_dir = tmp_path / "myapp"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\nversion = "0.1.0"\n[project.scripts]\nmyapp = "myapp.cli:main"\n'
    )
    _seed_file_node(conn, "myapp/src/myapp/__init__.py")
    ctx = RepoContext(org="t", repo="r")
    packages.refresh(
        conn, repo_root=tmp_path, ctx=ctx, manifests=packages.discover_manifest_packages(tmp_path, ctx=ctx)
    )
    entry_points.emit(conn, repo_root=tmp_path, ctx=ctx, skip_dirs=frozenset())

    app_desc = queries.describe_app(conn, name="myapp")
    pkg_desc = queries.describe_package(conn, name="myapp")
    assert app_desc is not None
    assert pkg_desc is not None
    assert app_desc.files == pkg_desc.files
    assert app_desc.files != []
    assert [ep.name for ep in app_desc.entry_points] == [ep.name for ep in pkg_desc.entry_points]
    assert app_desc.entry_points != []
    assert app_desc.app_kind == "cli"
    assert app_desc.app_signals == ["cli"]


def test_list_agent_plugins_alphabetical(conn: sqlite3.Connection) -> None:
    """list_agent_plugins returns alphabetically-sorted agent_plugin NodeRecords."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="agent_plugin",
                    name="zeta",
                    path=None,
                    line=None,
                    attrs={"ecosystem": "claude-code", "uri": "agent_plugin:o/r/zeta"},
                ),
                GraphNode(
                    kind="agent_plugin",
                    name="alpha",
                    path=None,
                    line=None,
                    attrs={"ecosystem": "claude-code", "uri": "agent_plugin:o/r/alpha"},
                ),
            ],
            edges=[],
        ),
    )
    assert [n.name for n in queries.list_agent_plugins(conn)] == ["alpha", "zeta"]


def test_resolve_selector_multi_kind(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="function", name="shared", path="a.py", line=1, attrs={}),
                GraphNode(kind="class", name="shared", path="b.py", line=2, attrs={}),
            ],
            edges=[],
        ),
    )
    rows = queries.resolve_selector(conn, selector="shared")
    assert {(r.kind, r.path) for r in rows} == {("function", "a.py"), ("class", "b.py")}


def test_resolve_selector_path_line(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="function", name="alpha", path="a.py", line=42, attrs={}),
                GraphNode(kind="function", name="beta", path="a.py", line=9, attrs={}),
            ],
            edges=[],
        ),
    )
    rows = queries.resolve_selector(conn, selector="a.py:42")
    assert [(r.name, r.line) for r in rows] == [("alpha", 42)]


def test_resolve_selector_path_line_ignores_in_package(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[GraphNode(kind="function", name="alpha", path="a.py", line=42, attrs={})],
            edges=[],
        ),
    )
    rows = queries.resolve_selector(conn, selector="a.py:42", in_package="nonexistent")
    assert [r.name for r in rows] == ["alpha"]


def test_resolve_selector_zero_match(conn: sqlite3.Connection) -> None:
    assert queries.resolve_selector(conn, selector="ghost") == []


def test_resolve_selector_in_package_narrows(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="package", name="pkg", path=None, line=None, attrs={}),
                GraphNode(kind="file", name="x.py", path="pkg/x.py", line=None, attrs={}),
                GraphNode(kind="function", name="target", path="pkg/x.py", line=1, attrs={}),
                GraphNode(kind="function", name="target", path="other/y.py", line=1, attrs={}),
            ],
            edges=[
                GraphEdge(src=("package", "pkg", None), dst=("file", "x.py", "pkg/x.py"), kind="contains", attrs={}),
            ],
        ),
    )
    rows = queries.resolve_selector(conn, selector="target", in_package="pkg")
    assert [r.path for r in rows] == ["pkg/x.py"]


def test_containing_package(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="package", name="pkg", path=None, line=None, attrs={}),
                GraphNode(kind="file", name="x.py", path="pkg/x.py", line=None, attrs={}),
            ],
            edges=[
                GraphEdge(src=("package", "pkg", None), dst=("file", "x.py", "pkg/x.py"), kind="contains", attrs={}),
            ],
        ),
    )
    assert queries.containing_package(conn, path="pkg/x.py") == "pkg"
    assert queries.containing_package(conn, path="nowhere.py") is None


def test_describe_symbol_dossier(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="package", name="foo", path=None, line=None, attrs={}),
                GraphNode(kind="file", name="a.py", path="foo/a.py", line=None, attrs={}),
                GraphNode(kind="file", name="init", path="foo/__init__.py", line=None, attrs={}),
                GraphNode(kind="function", name="process", path="foo/a.py", line=42, attrs={}),
                GraphNode(kind="function", name="run_scan", path="foo/a.py", line=1, attrs={}),
                GraphNode(kind="function", name="validate", path="foo/a.py", line=80, attrs={}),
            ],
            edges=[
                GraphEdge(src=("package", "foo", None), dst=("file", "a.py", "foo/a.py"), kind="contains", attrs={}),
                GraphEdge(
                    src=("function", "run_scan", "foo/a.py"), dst=("function", "process", None), kind="calls", attrs={}
                ),
                GraphEdge(
                    src=("function", "process", "foo/a.py"), dst=("function", "validate", None), kind="calls", attrs={}
                ),
                GraphEdge(
                    src=("file", "init", "foo/__init__.py"), dst=("function", "process", None), kind="exports", attrs={}
                ),
            ],
        ),
    )
    resolve.sweep(conn)
    desc = queries.describe_symbol(conn, kind="function", name="process", path="foo/a.py", line=42)
    assert desc is not None
    assert (desc.kind, desc.name, desc.path, desc.line) == ("function", "process", "foo/a.py", 42)
    assert desc.package == "foo"
    assert desc.exported_from == "foo/__init__.py"
    assert {c.name for c in desc.callers} == {"run_scan"}
    assert {c.name for c in desc.callees} == {"validate"}


def test_describe_symbol_unexported_no_callees(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="class", name="Widget", path="foo/a.py", line=5, attrs={}),
            ],
            edges=[],
        ),
    )
    desc = queries.describe_symbol(conn, kind="class", name="Widget", path="foo/a.py", line=5)
    assert desc is not None
    assert desc.exported_from is None
    assert desc.callees == []
    assert desc.callers == []
    assert desc.package is None


def test_describe_symbol_not_found(conn: sqlite3.Connection) -> None:
    assert queries.describe_symbol(conn, kind="function", name="ghost") is None


# ============================================================================
# MatchRecord / build_menu
# ============================================================================


def test_build_menu_in_package_form(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="package", name="foo", path=None, line=None, attrs={}),
                GraphNode(kind="file", name="a.py", path="foo/a.py", line=None, attrs={}),
                GraphNode(kind="function", name="run", path="foo/a.py", line=10, attrs={}),
                GraphNode(kind="class", name="run", path="foo/a.py", line=20, attrs={}),
            ],
            edges=[
                GraphEdge(src=("package", "foo", None), dst=("file", "a.py", "foo/a.py"), kind="contains", attrs={}),
            ],
        ),
    )
    matches = queries.resolve_selector(conn, selector="run")
    menu = queries.build_menu(conn, matches)
    cmds = {m.kind: m.command for m in menu}
    assert cmds["function"] == "gw graph describe run --kind function --in-package foo"
    assert cmds["class"] == "gw graph describe run --kind class --in-package foo"
    assert {m.address for m in menu} == {"foo/a.py:10", "foo/a.py:20"}


def test_build_menu_collision_uses_path_line(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="package", name="foo", path=None, line=None, attrs={}),
                GraphNode(kind="file", name="a.py", path="foo/a.py", line=None, attrs={}),
                GraphNode(kind="file", name="b.py", path="foo/b.py", line=None, attrs={}),
                GraphNode(kind="method", name="save", path="foo/a.py", line=10, attrs={}),
                GraphNode(kind="method", name="save", path="foo/b.py", line=30, attrs={}),
            ],
            edges=[
                GraphEdge(src=("package", "foo", None), dst=("file", "a.py", "foo/a.py"), kind="contains", attrs={}),
                GraphEdge(src=("package", "foo", None), dst=("file", "b.py", "foo/b.py"), kind="contains", attrs={}),
            ],
        ),
    )
    matches = queries.resolve_selector(conn, selector="save")
    menu = queries.build_menu(conn, matches)
    assert {m.command for m in menu} == {
        "gw graph describe foo/a.py:10",
        "gw graph describe foo/b.py:30",
    }


def test_build_menu_dependency_form(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="dependency", name="boto3", path=None, line=None, attrs={"ecosystem": "pypi"}),
            ],
            edges=[],
        ),
    )
    menu = queries.build_menu(conn, queries.resolve_selector(conn, selector="boto3"))
    assert menu[0].kind == "dependency"
    assert menu[0].address == ""
    assert menu[0].command == "gw graph describe boto3 --kind dependency"


def test_build_menu_test_suite_kind_uses_the_core_describe_spelling(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[GraphNode(kind="test_suite", name="core_tests", path=None, line=None, attrs={})],
            edges=[],
        ),
    )
    menu = queries.build_menu(conn, queries.resolve_selector(conn, selector="core_tests"))
    assert menu[0].command == "gw graph describe core_tests --kind test_suite"


def test_build_menu_dependency_with_synthetic_path(conn: sqlite3.Connection) -> None:
    """A dependency node carrying a synthetic path/None line is addressed by the
    `<org>/<repo>/<ecosystem>/<name>` identifier (not --in-package None, and not the
    `--ecosystem` flag that no longer exists) with a blank address (not ":None")."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="dependency",
                    name="boto3",
                    path="dependency:acme/app:pypi:boto3",
                    line=None,
                    attrs={"ecosystem": "pypi"},
                ),
            ],
            edges=[],
        ),
    )
    menu = queries.build_menu(conn, queries.resolve_selector(conn, selector="boto3"))
    assert len(menu) == 1
    assert menu[0].kind == "dependency"
    assert menu[0].command == "gw graph describe acme/app/pypi/boto3 --kind dependency"
    assert menu[0].address == ""


def test_build_menu_builtin_uses_the_folded_uri_identifier(conn: sqlite3.Connection) -> None:
    """Builtin nodes key on (name=module, path=language); describe takes the two
    folded into one `builtin:<language>/<module>` identifier, not a bare name."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="builtin",
                    name="json",
                    path="python",
                    line=None,
                    attrs={"language": "python", "module_name": "json"},
                ),
            ],
            edges=[],
        ),
    )
    menu = queries.build_menu(conn, queries.resolve_selector(conn, selector="json"))
    assert menu[0].kind == "builtin"
    assert menu[0].address == ""
    assert menu[0].command == "gw graph describe builtin:python/json --kind builtin"


def test_build_menu_code_symbol_no_package_uses_path_line(conn: sqlite3.Connection) -> None:
    """A code symbol with a path but no containing package/app falls back to the
    unambiguous path:line command (there is no package name to narrow by)."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="function", name="orphan", path="loose/a.py", line=7, attrs={}),
            ],
            edges=[],
        ),
    )
    menu = queries.build_menu(conn, queries.resolve_selector(conn, selector="orphan"))
    assert len(menu) == 1
    assert menu[0].command == "gw graph describe loose/a.py:7"
    assert menu[0].address == "loose/a.py:7"


def test_containing_package_app(conn: sqlite3.Connection) -> None:
    """containing_package returns the owning app name for an app-resident file."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="app", name="webapp", path=None, line=None, attrs={}),
                GraphNode(kind="file", name="x.py", path="webapp/x.py", line=None, attrs={}),
            ],
            edges=[
                GraphEdge(src=("app", "webapp", None), dst=("file", "x.py", "webapp/x.py"), kind="contains", attrs={}),
            ],
        ),
    )
    assert queries.containing_package(conn, path="webapp/x.py") == "webapp"


def test_describe_symbol_in_package_app(conn: sqlite3.Connection) -> None:
    """describe_symbol narrows by an app name (in_package filter spans package+app)."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="app", name="webapp", path=None, line=None, attrs={}),
                GraphNode(kind="file", name="a.py", path="webapp/a.py", line=None, attrs={}),
                GraphNode(kind="function", name="handler", path="webapp/a.py", line=3, attrs={}),
            ],
            edges=[
                GraphEdge(src=("app", "webapp", None), dst=("file", "a.py", "webapp/a.py"), kind="contains", attrs={}),
            ],
        ),
    )
    desc = queries.describe_symbol(conn, kind="function", name="handler", in_package="webapp")
    assert desc is not None
    assert (desc.kind, desc.name, desc.path, desc.line) == ("function", "handler", "webapp/a.py", 3)
    assert desc.package == "webapp"


def test_find_in_package_matches_app_name(conn: sqlite3.Connection) -> None:
    """find --in-package narrows by app name too, not just package name
    (parity with describe_symbol / containing_package)."""
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="app", name="webapp", path=None, line=None, attrs={}),
                GraphNode(kind="file", name="m.py", path="webapp/m.py", line=None, attrs={}),
                GraphNode(kind="function", name="handler", path="webapp/m.py", line=3, attrs={}),
                GraphNode(kind="function", name="handler", path="other/z.py", line=3, attrs={}),
            ],
            edges=[
                GraphEdge(src=("app", "webapp", None), dst=("file", "m.py", "webapp/m.py"), kind="contains", attrs={}),
            ],
        ),
    )
    rows = queries.find(conn, name="handler", in_package="webapp")
    assert [r.path for r in rows] == ["webapp/m.py"]


def test_describe_path_includes_exports(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
                GraphNode(kind="function", name="alpha", path="a.py", line=10, attrs={}),
                GraphNode(kind="file", name="b.py", path="b.py", line=None, attrs={}),
            ],
            edges=[
                GraphEdge(src=("file", "a.py", "a.py"), dst=("function", "alpha", None), kind="exports", attrs={}),
                GraphEdge(src=("file", "a.py", "a.py"), dst=("file", "b.py", None), kind="imports", attrs={}),
            ],
        ),
    )
    resolve.sweep(conn)
    desc = queries.describe_path(conn, path="a.py")
    assert desc is not None
    assert [(e.kind, e.name, e.line) for e in desc.exports] == [("function", "alpha", 10)]
    assert [i.path for i in desc.imports] == ["b.py"]


def _seed_prod_test_target(conn: sqlite3.Connection) -> None:
    """P (prod) -> T (test) -> X (prod). T lives in a test file (is_test=True).

    Call edges: P calls T, T calls X. So X's callers are {T (depth 1),
    P (depth 2, only via T)}; P's callees are {T (depth 1), X (depth 2,
    only via T)}.
    """
    nodes = [
        GraphNode(kind="file", name="prod.py", path="prod.py", line=None, attrs={}),
        GraphNode(kind="file", name="test_prod.py", path="test_prod.py", line=None, attrs={"is_test": True}),
        GraphNode(kind="function", name="P", path="prod.py", line=1, attrs={}),
        GraphNode(kind="function", name="T", path="test_prod.py", line=1, attrs={}),
        GraphNode(kind="function", name="X", path="prod.py", line=10, attrs={}),
    ]
    edges = [
        GraphEdge(src=("function", "P", "prod.py"), dst=("function", "T", None), kind="calls", attrs={}),
        GraphEdge(src=("function", "T", "test_prod.py"), dst=("function", "X", None), kind="calls", attrs={}),
    ]
    upsert.upsert_records(conn, GraphRecords(nodes=nodes, edges=edges))
    resolve.sweep(conn)


def test_callers_excludes_test_symbols_by_default(conn: sqlite3.Connection) -> None:
    _seed_prod_test_target(conn)
    rows = queries.callers(conn, name="X", depth=3)
    assert {r.name for r in rows} == set()


def test_callers_include_tests_restores_full_walk(conn: sqlite3.Connection) -> None:
    _seed_prod_test_target(conn)
    rows = queries.callers(conn, name="X", depth=3, include_test_files=True)
    assert {r.name for r in rows} == {"T", "P"}


def test_callees_excludes_test_symbols_by_default(conn: sqlite3.Connection) -> None:
    _seed_prod_test_target(conn)
    rows = queries.callees(conn, name="P", depth=3)
    assert {r.name for r in rows} == set()


def test_callees_include_tests_restores_full_walk(conn: sqlite3.Connection) -> None:
    _seed_prod_test_target(conn)
    rows = queries.callees(conn, name="P", depth=3, include_test_files=True)
    assert {r.name for r in rows} == {"T", "X"}


def test_non_test_caller_unaffected_by_default(conn: sqlite3.Connection) -> None:
    _seed_call_chain(conn)
    rows = queries.callers(conn, name="gamma", depth=3)
    assert {r.name for r in rows} == {"alpha", "beta"}


def test_non_test_callee_unaffected_by_default(conn: sqlite3.Connection) -> None:
    # _seed_call_chain has no test files; alpha->beta->gamma in a.py.
    _seed_call_chain(conn)
    rows = queries.callees(conn, name="alpha", depth=3)
    assert {r.name for r in rows} == {"beta", "gamma"}


def test_describe_symbol_inherits_test_exclusion(conn: sqlite3.Connection) -> None:
    _seed_prod_test_target(conn)
    desc = queries.describe_symbol(conn, kind="function", name="X")
    assert desc is not None
    assert {c.name for c in desc.callers} == set()


# ============================================================================
# suffix-aware code-symbol queries for qualified method names.
# ============================================================================


def test_find_bare_leaf_matches_qualified_methods(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="package", name="foo", path=None, line=None, attrs={}),
                GraphNode(kind="file", name="a.py", path="foo/a.py", line=None, attrs={}),
                GraphNode(kind="method", name="Foo.save", path="foo/a.py", line=5, attrs={}),
                GraphNode(kind="method", name="Bar.save", path="foo/a.py", line=15, attrs={}),
            ],
            edges=[
                GraphEdge(src=("package", "foo", None), dst=("file", "a.py", "foo/a.py"), kind="contains", attrs={}),
            ],
        ),
    )
    # Bare leaf matches both qualified names.
    assert {n.name for n in queries.find(conn, name="save")} == {"Foo.save", "Bar.save"}
    # Qualified name matches only the exact node.
    assert {n.name for n in queries.find(conn, name="Foo.save")} == {"Foo.save"}
    # Two same-file methods with distinct qualified names → two distinct menu commands.
    menu = queries.build_menu(conn, queries.resolve_selector(conn, selector="save"))
    cmds = {m.command for m in menu}
    assert len(cmds) == 2
    # Both commands must carry the qualified names (not the bare leaf).
    assert any("Foo.save" in c for c in cmds)
    assert any("Bar.save" in c for c in cmds)


def test_describe_symbol_bare_leaf_finds_qualified_method(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(kind="file", name="a.py", path="a.py", line=None, attrs={}),
                GraphNode(kind="method", name="Util.format", path="a.py", line=5, attrs={}),
            ],
            edges=[],
        ),
    )
    desc = queries.describe_symbol(conn, kind="method", name="format")
    assert desc is not None and desc.name == "Util.format"


def test_describe_symbol_includes_token_count(seeded_db) -> None:
    desc = queries.describe_symbol(seeded_db, kind="function", name="foo")
    assert desc is not None
    assert isinstance(desc.token_count, int)
    assert desc.token_count > 0


def test_describe_path_includes_token_count(seeded_db) -> None:
    row = seeded_db.execute("SELECT path FROM nodes WHERE kind='file' AND path LIKE '%foo.py' LIMIT 1").fetchone()
    assert row is not None
    desc = queries.describe_path(seeded_db, path=row[0])
    assert desc is not None
    assert isinstance(desc.token_count, int)
    assert desc.token_count > 0
    assert any(c.attrs.get("token_count") for c in desc.children)


# ============================================================================
# shared resolve_entry_point helpers.
# ============================================================================


def test_resolve_entry_point_qualified(seeded_db: sqlite3.Connection) -> None:
    from code_graph_io import queries

    desc, ambiguous = queries.resolve_entry_point(seeded_db, "mypkg:mypkg-run")
    assert ambiguous == []
    assert desc is not None and desc.name == "mypkg-run"


def test_resolve_entry_point_bare_unique(seeded_db: sqlite3.Connection) -> None:
    from code_graph_io import queries

    desc, ambiguous = queries.resolve_entry_point(seeded_db, "mypkg-run")
    assert ambiguous == []
    assert desc is not None and desc.name == "mypkg-run"


def test_resolve_entry_point_bare_ambiguous(empty_db: sqlite3.Connection) -> None:
    """Two packages declaring the same entry name → (None, [both names]).

    The seeded fixture only has one `mypkg-run`, so the >1-match branch is
    exercised against a minimal hand-built graph: two `package` nodes each
    with a `declares_entry_point` edge to a same-named `entry_point` node."""
    from code_graph_io import queries

    cur = empty_db.execute("INSERT INTO nodes (kind, name, attrs_json) VALUES ('package', 'alpha', '{}')")
    alpha_id = cur.lastrowid
    cur = empty_db.execute("INSERT INTO nodes (kind, name, attrs_json) VALUES ('package', 'beta', '{}')")
    beta_id = cur.lastrowid
    cur = empty_db.execute("INSERT INTO nodes (kind, name, attrs_json) VALUES ('entry_point', 'shared-run', '{}')")
    ep_alpha_id = cur.lastrowid
    cur = empty_db.execute("INSERT INTO nodes (kind, name, attrs_json) VALUES ('entry_point', 'shared-run', '{}')")
    ep_beta_id = cur.lastrowid
    empty_db.execute(
        "INSERT INTO edges (src, dst, kind) VALUES (?, ?, 'declares_entry_point')",
        (alpha_id, ep_alpha_id),
    )
    empty_db.execute(
        "INSERT INTO edges (src, dst, kind) VALUES (?, ?, 'declares_entry_point')",
        (beta_id, ep_beta_id),
    )

    desc, ambiguous = queries.resolve_entry_point(empty_db, "shared-run")
    assert desc is None
    assert sorted(ambiguous) == ["alpha", "beta"]


def test_resolve_entry_point_missing(seeded_db: sqlite3.Connection) -> None:
    from code_graph_io import queries

    desc, ambiguous = queries.resolve_entry_point(seeded_db, "no-such-ep")
    assert desc is None and ambiguous == []


def _seed_scoped_dependency(
    conn: sqlite3.Connection, *, org: str, repo: str, name: str, versions: list[str], consumer: str
) -> None:
    upsert.set_current_repo(conn, f"repo:{org}/{repo}")
    try:
        upsert.upsert_records(
            conn,
            GraphRecords(
                nodes=[
                    GraphNode(
                        kind="dependency",
                        name=name,
                        path=f"dependency:{org}/{repo}:pypi:{name}",
                        line=None,
                        attrs={
                            "uri": f"dependency:{org}/{repo}/pypi/{name}",
                            "ecosystem": "pypi",
                            "name": name,
                            "versions_in_use": versions,
                        },
                    ),
                    GraphNode(
                        kind="package",
                        name=consumer,
                        path=f"{repo}/{consumer}",
                        line=None,
                        attrs={"uri": f"pkg:{org}/{repo}/{consumer}"},
                    ),
                ],
                edges=[
                    GraphEdge(
                        src=("package", consumer, f"{repo}/{consumer}"),
                        dst=("dependency", name, f"dependency:{org}/{repo}:pypi:{name}"),
                        kind="used_by",
                        attrs={},
                    )
                ],
            ),
        )
    finally:
        upsert.set_current_repo(conn, None)


def test_describe_dependency_by_uri_returns_the_requested_repository(conn: sqlite3.Connection) -> None:
    _seed_scoped_dependency(conn, org="acme", repo="a", name="requests", versions=["requests>=2"], consumer="alpha")
    _seed_scoped_dependency(conn, org="acme", repo="b", name="requests", versions=["requests==2.31"], consumer="beta")

    a = queries.describe_dependency(conn, uri="dependency:acme/a/pypi/requests")
    b = queries.describe_dependency(conn, repo="repo:acme/b", ecosystem="pypi", name="requests")

    assert a is not None and b is not None
    assert (a.repository, a.versions_in_use, a.used_by) == ("repo:acme/a", ["requests>=2"], ["pkg:acme/a/alpha"])
    assert (b.repository, b.versions_in_use, b.used_by) == ("repo:acme/b", ["requests==2.31"], ["pkg:acme/b/beta"])


def test_describe_dependency_requires_a_complete_identity(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="uri"):
        queries.describe_dependency(conn, ecosystem="pypi", name="requests")


def test_consumer_packages_for_dependency_is_scoped_to_the_node(conn: sqlite3.Connection) -> None:
    _seed_scoped_dependency(conn, org="acme", repo="a", name="requests", versions=[], consumer="alpha")
    _seed_scoped_dependency(conn, org="acme", repo="b", name="requests", versions=[], consumer="beta")

    assert queries.consumer_packages(conn, kind="dependency", entity_uri="dependency:acme/a/pypi/requests") == (
        "alpha",
    )


def test_describe_package_unions_every_scoped_implemented_node(conn: sqlite3.Connection) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[
                GraphNode(
                    kind="package",
                    name="library",
                    path="lib/library",
                    line=None,
                    attrs={"uri": "pkg:acme/lib/library", "language": "python"},
                )
            ],
            edges=[],
        ),
    )
    _seed_scoped_dependency(conn, org="acme", repo="a", name="library", versions=["library>=1"], consumer="alpha")
    _seed_scoped_dependency(conn, org="acme", repo="b", name="library", versions=["library>=2"], consumer="beta")
    for repo in ("a", "b"):
        upsert.upsert_records(
            conn,
            GraphRecords(
                nodes=[],
                edges=[
                    GraphEdge(
                        src=("dependency", "library", f"dependency:acme/{repo}:pypi:library"),
                        dst=("package", "library", "lib/library"),
                        kind="implemented_by",
                        attrs={},
                    )
                ],
            ),
        )

    description = queries.describe_package(conn, name="library", uri="pkg:acme/lib/library")

    assert description is not None
    assert description.used_by == ["pkg:acme/a/alpha", "pkg:acme/b/beta"]
    assert description.versions_in_use == ["library>=1", "library>=2"]


def test_build_menu_emits_the_four_part_dependency_identifier(conn: sqlite3.Connection) -> None:
    _seed_scoped_dependency(conn, org="acme", repo="a", name="requests", versions=[], consumer="alpha")
    matches = [m for m in queries.resolve_selector(conn, selector="requests") if m.kind == "dependency"]

    menu = queries.build_menu(conn, matches)

    assert [entry.command for entry in menu] == ["gw graph describe acme/a/pypi/requests --kind dependency"]


def test_scoped_npm_name_survives_describe_and_menu(conn: sqlite3.Connection) -> None:
    upsert.set_current_repo(conn, "repo:o/r")
    try:
        upsert.upsert_records(
            conn,
            GraphRecords(
                nodes=[
                    GraphNode(
                        kind="dependency",
                        name="@scope/pkg",
                        path="dependency:o/r:npm:@scope/pkg",
                        line=None,
                        attrs={"uri": "dependency:o/r/npm/@scope/pkg", "ecosystem": "npm", "name": "@scope/pkg"},
                    )
                ],
                edges=[],
            ),
        )
    finally:
        upsert.set_current_repo(conn, None)

    d = queries.describe_dependency(conn, uri="dependency:o/r/npm/@scope/pkg")
    assert d is not None and (d.name, d.ecosystem, d.repository) == ("@scope/pkg", "npm", "repo:o/r")
    matches = [m for m in queries.resolve_selector(conn, selector="@scope/pkg") if m.kind == "dependency"]
    assert [e.command for e in queries.build_menu(conn, matches)] == [
        "gw graph describe o/r/npm/@scope/pkg --kind dependency"
    ]

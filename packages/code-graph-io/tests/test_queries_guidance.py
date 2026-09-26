"""`external_dependencies_of`, `internal_dependency_uris_of` and `file_uris` — the reads the affects closure needs."""

from __future__ import annotations

from pathlib import Path

from code_graph_io import testing as gtesting


def _seed(db: Path):
    store = gtesting.open_store(db, create=True)
    c = store._conn
    c.execute("BEGIN")
    rows = [
        (1, "package", "okf-ext", "packages/okf-ext", "pkg:o/r/okf-ext", "repo:o/r"),
        (2, "app", "cli", "packages/cli", "app:o/r/cli", "repo:o/r"),
        (3, "dependency", "ruamel.yaml", None, "dependency:o/r/pypi/ruamel.yaml", "repo:o/r"),
        (4, "dependency", "typer", None, "dependency:pypi/typer", None),  # legacy unscoped
        (5, "builtin", "json", None, "builtin:python/json", None),
        (6, "dependency", "@scope/pkg", None, "dependency:o/r/npm/@scope/pkg", "repo:o/r"),
        (7, "file", "README.md", "README.md", "file:o/r/README.md", "repo:o/r"),
        (8, "file", "README.md", "README.md", "file:o/ui/README.md", "repo:o/ui"),
        (9, "file", "x.py", "x.py", None, "repo:o/r"),  # no uri: never listed
        # Same-named packages in two repos, only one of which has a dependency edge:
        # `internal_dependency_uris_of` must not let a same-named sibling's edges leak.
        (10, "package", "core", "packages/core", "pkg:o/r/core", "repo:o/r"),
        (11, "package", "proto", "packages/proto", "pkg:o/r/proto", "repo:o/r"),
        (12, "package", "core", "packages/core", "pkg:o/ui/core", "repo:o/ui"),
        (13, "package", "proto", "packages/proto", "pkg:o/ui/proto", "repo:o/ui"),
    ]
    c.executemany("INSERT INTO nodes (id, kind, name, path, uri, repo) VALUES (?,?,?,?,?,?)", rows)
    edges = [
        (1, 3, "used_by"),
        (1, 4, "used_by"),
        (1, 5, "used_by"),
        (1, 6, "used_by"),
        (12, 13, "depends_on_package"),  # only ui/core -> ui/proto; r/core has no dependencies
    ]
    c.executemany("INSERT INTO edges (src, dst, kind) VALUES (?,?,?)", edges)
    c.execute("INSERT INTO edges (src, dst, kind) VALUES (2, 3, 'used_by')")
    c.commit()
    return store


def test_external_dependencies_of_returns_repo_scoped_dependency_uris_only(tmp_path: Path) -> None:
    store = _seed(tmp_path / "code.db")
    try:
        assert store.external_dependencies_of(uri="pkg:o/r/okf-ext") == [
            "dependency:o/r/npm/@scope/pkg",
            "dependency:o/r/pypi/ruamel.yaml",
        ]
        assert store.external_dependencies_of(uri="app:o/r/cli") == ["dependency:o/r/pypi/ruamel.yaml"]
        assert store.external_dependencies_of(uri="pkg:o/r/missing") == []
    finally:
        store.close()


def test_file_uris_lists_every_file_uri_sorted(tmp_path: Path) -> None:
    store = _seed(tmp_path / "code.db")
    try:
        assert store.file_uris() == ["file:o/r/README.md", "file:o/ui/README.md"]
    finally:
        store.close()


def test_internal_dependency_uris_of_is_scoped_to_the_source_repository(tmp_path: Path) -> None:
    store = _seed(tmp_path / "code.db")
    try:
        # r/core has no depends_on_package edges of its own -- the same-named
        # ui/core -> ui/proto edge must not leak onto it via a name match.
        assert store.internal_dependency_uris_of(uri="pkg:o/r/core") == []
        assert store.internal_dependency_uris_of(uri="pkg:o/ui/core") == ["pkg:o/ui/proto"]
        assert store.internal_dependency_uris_of(uri="pkg:o/r/missing") == []
    finally:
        store.close()


def test_external_dependencies_of_filters_legacy_nodes_structurally(tmp_path: Path) -> None:
    """Scoped means `dep.repo = src.repo`, not a URI shape.

    A slashy legacy name (`dependency:go/github.com/x/y`, `repo` NULL) has four
    `/`-parts and would pass a part count; a repo-scoped node whose URI has
    only three parts is still the source repository's dependency; another
    repository's scoped dependency is not.
    """
    store = _seed(tmp_path / "code.db")
    try:
        c = store._conn
        c.executemany(
            "INSERT INTO nodes (id, kind, name, path, uri, repo) VALUES (?,?,?,?,?,?)",
            [
                (20, "dependency", "github.com/x/y", None, "dependency:go/github.com/x/y", None),
                (21, "dependency", "odd", None, "dependency:o/r/odd", "repo:o/r"),
                (22, "dependency", "other", None, "dependency:o/ui/pypi/other", "repo:o/ui"),
            ],
        )
        c.executemany(
            "INSERT INTO edges (src, dst, kind) VALUES (?,?,?)",
            [(1, 20, "used_by"), (1, 21, "used_by"), (1, 22, "used_by")],
        )
        c.commit()
        assert store.external_dependencies_of(uri="pkg:o/r/okf-ext") == [
            "dependency:o/r/npm/@scope/pkg",
            "dependency:o/r/odd",
            "dependency:o/r/pypi/ruamel.yaml",
        ]
    finally:
        store.close()

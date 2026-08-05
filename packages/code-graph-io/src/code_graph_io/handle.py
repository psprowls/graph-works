"""Public graph access surface: GraphReader / GraphStore handles.

This is the ONLY sanctioned way for code outside code-graph-io to read or write the
code graph. The handle methods thin-delegate to the module-internal
queries/upsert/resolve functions; callers never see a
``sqlite3.Connection`` or build the ``code.db`` path themselves.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any

from code_graph_io import graphml, queries, resolve, store, upsert
from code_graph_io.paths import graph_dir
from code_graph_io.queries import (
    AgentPluginDescription,
    AppDescription,
    BuiltinDescription,
    CallRecord,
    ChildNode,
    DependencyDescription,
    EntryPointDescription,
    ExporterRecord,
    ExportRecord,
    ImporterRecord,
    ImportRecord,
    MatchRecord,
    NodeRecord,
    PackageDescription,
    PathDescription,
    RepoDescription,
    SuiteDescription,
    SymbolDescription,
)

if TYPE_CHECKING:
    from code_parser.projections.graph import GraphRecords


def _db_path(workspace: Path) -> Path:
    return graph_dir(Path(workspace)) / "code.db"


class GraphReader:
    """Read-only view over the code graph. Wraps a read-only sqlite3 connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- lifecycle ---
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> GraphReader:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- find / resolve ---
    def find(
        self, *, name: str | None = None, kind: str | None = None, in_package: str | None = None
    ) -> list[NodeRecord]:
        return queries.find(self._conn, name=name, kind=kind, in_package=in_package)

    def resolve_selector(self, *, selector: str, in_package: str | None = None) -> list[NodeRecord]:
        return queries.resolve_selector(self._conn, selector=selector, in_package=in_package)

    def build_menu(self, matches: list[NodeRecord]) -> list[MatchRecord]:
        return queries.build_menu(self._conn, matches)

    def containing_package(self, *, path: str) -> str | None:
        return queries.containing_package(self._conn, path=path)

    # --- describe_* (delegating with self._conn) ---
    def describe_symbol(
        self,
        *,
        kind: str,
        name: str,
        in_package: str | None = None,
        path: str | None = None,
        line: int | None = None,
    ) -> SymbolDescription | None:
        return queries.describe_symbol(self._conn, kind=kind, name=name, in_package=in_package, path=path, line=line)

    def describe_package(self, *, name: str) -> PackageDescription | None:
        return queries.describe_package(self._conn, name=name)

    def describe_app(self, *, name: str) -> AppDescription | None:
        return queries.describe_app(self._conn, name=name)

    def describe_path(self, *, path: str) -> PathDescription | None:
        return queries.describe_path(self._conn, path=path)

    def describe_repository(self) -> RepoDescription | None:
        return queries.describe_repository(self._conn)

    def describe_entry_point(self, *, package_name: str, entry_name: str) -> EntryPointDescription | None:
        return queries.describe_entry_point(self._conn, package_name=package_name, entry_name=entry_name)

    def describe_test_suite(self, *, suite_name: str) -> SuiteDescription | None:
        return queries.describe_test_suite(self._conn, suite_name=suite_name)

    def describe_dependency(self, *, ecosystem: str, name: str) -> DependencyDescription | None:
        return queries.describe_dependency(self._conn, ecosystem=ecosystem, name=name)

    def describe_builtin(self, *, language: str, module_name: str) -> BuiltinDescription | None:
        return queries.describe_builtin(self._conn, language=language, module_name=module_name)

    def describe_agent_plugin(self, *, name: str) -> AgentPluginDescription | None:
        return queries.describe_agent_plugin(self._conn, name=name)

    # --- call graph ---
    def callers(self, *, name: str, depth: int = 3, include_test_files: bool = False) -> list[CallRecord]:
        return queries.callers(self._conn, name=name, depth=depth, include_test_files=include_test_files)

    def callees(self, *, name: str, depth: int = 3, include_test_files: bool = False) -> list[CallRecord]:
        return queries.callees(self._conn, name=name, depth=depth, include_test_files=include_test_files)

    # --- imports / exports ---
    def imports(self, *, path: str) -> list[ImportRecord]:
        return queries.imports(self._conn, path=path)

    def imported_by(self, *, path: str, symbol: str | None = None, depth: int = 1) -> list[ImporterRecord]:
        return queries.imported_by(self._conn, path=path, symbol=symbol, depth=depth)

    def exports(self, *, path: str) -> list[ExportRecord]:
        return queries.exports(self._conn, path=path)

    def exported_by(self, *, name: str) -> list[ExporterRecord]:
        return queries.exported_by(self._conn, name=name)

    # --- trees / relations ---
    def children_tree(self, *, node: NodeRecord, depth: int) -> list[ChildNode]:
        return queries.children_tree(self._conn, node=node, depth=depth)

    def children_for(
        self,
        *,
        kind: str,
        name: str | None = None,
        path: str | None = None,
        line: int | None = None,
        uri: str | None = None,
        depth: int | None = None,
    ) -> tuple[list[ChildNode], int]:
        return queries.children_for(self._conn, kind=kind, name=name, path=path, line=line, uri=uri, depth=depth)

    def tests_for_package(self, *, package_name: str) -> list[SuiteDescription]:
        return queries.tests_for_package(self._conn, package_name=package_name)

    def entry_points_for_package(self, *, package_name: str) -> list[EntryPointDescription]:
        return queries.entry_points_for_package(self._conn, package_name=package_name)

    def internal_dependencies_of(self, *, name: str) -> list[str]:
        return queries.internal_dependencies_of(self._conn, name=name)

    def resolve_entry_point(self, raw: str) -> tuple[EntryPointDescription | None, list[str]]:
        return queries.resolve_entry_point(self._conn, raw)

    # --- list_* ---
    def list_repositories(self) -> list[NodeRecord]:
        return queries.list_repositories(self._conn)

    def list_packages(self) -> list[NodeRecord]:
        return queries.list_packages(self._conn)

    def list_apps(self) -> list[NodeRecord]:
        return queries.list_apps(self._conn)

    def list_entry_points(self) -> list[NodeRecord]:
        return queries.list_entry_points(self._conn)

    def list_test_suites(self) -> list[NodeRecord]:
        return queries.list_test_suites(self._conn)

    def list_dependencies(self) -> list[NodeRecord]:
        return queries.list_dependencies(self._conn)

    def list_builtins(self) -> list[NodeRecord]:
        return queries.list_builtins(self._conn)

    def list_agent_plugins(self) -> list[NodeRecord]:
        return queries.list_agent_plugins(self._conn)

    def list_scripts(self) -> list[NodeRecord]:
        return queries.list_scripts(self._conn)

    # --- raw dump (ops_dump) ---
    def dump_sql(self) -> Iterator[str]:
        return self._conn.iterdump()

    # --- graphml export ---
    def to_graphml(self) -> str:
        return graphml.to_graphml(self._conn)

    # --- cli/core raw-SQL ports ---
    def metadata(self, key: str) -> str | None:
        return queries.metadata(self._conn, key)

    def node_count(self) -> int:
        return queries.node_count(self._conn)

    def node_counts_by_kind(self) -> dict[str, int]:
        return queries.node_counts_by_kind(self._conn)

    def edge_counts_by_kind(self) -> dict[str, int]:
        return queries.edge_counts_by_kind(self._conn)

    def languages(self) -> list[str]:
        return queries.languages(self._conn)

    def file_paths(self) -> list[str]:
        return queries.file_paths(self._conn)

    def file_paths_in_package(self, name: str) -> list[str]:
        return queries.file_paths_in_package(self._conn, name)

    def file_attrs(self, path: str) -> dict[str, Any] | None:
        return queries.file_attrs(self._conn, path)

    def files_in_node(self, node_id: int) -> list[tuple[Any, ...]]:
        return queries.files_in_node(self._conn, node_id)

    def symbol_names_under_files(
        self, file_ids: Iterable[int], kinds: tuple[str, ...] = ("class", "function", "method")
    ) -> list[str]:
        return queries.symbol_names_under_files(self._conn, file_ids, kinds)

    def declared_entry_points(self) -> list[tuple[Any, ...]]:
        return queries.declared_entry_points(self._conn)

    def node_exists(self, *, kind: str, name: str) -> bool:
        return queries.node_exists(self._conn, kind, name)

    # --- wiki-io entity-lookup / index-generation ports ---
    def package_for_file(self, *, path: str) -> tuple[str, str] | None:
        return queries.package_for_file(self._conn, path)

    def entity_by_name(
        self, *, name: str, kinds: tuple[str, ...] = ("package", "class", "function", "method")
    ) -> list[tuple[Any, ...]]:
        return queries.entity_by_name(self._conn, name, kinds)

    def package_or_app_by_dir(self, *, path: str) -> tuple[Any, ...] | None:
        return queries.package_or_app_by_dir(self._conn, path)

    def consumer_packages(self, *, kind: str, entity_uri: str = "", entity_name: str = "") -> tuple[str, ...]:
        return queries.consumer_packages(self._conn, kind=kind, entity_uri=entity_uri, entity_name=entity_name)


class GraphStore(GraphReader):
    """Read-write handle. Adds the mutating surface."""

    def upsert_records(self, records: GraphRecords) -> None:
        upsert.upsert_records(self._conn, records)

    def set_current_repo(self, repo_uri: str | None) -> None:
        upsert.set_current_repo(self._conn, repo_uri)

    def resolve_file_imports(self, repo_root: Path) -> None:
        resolve.resolve_file_imports(self._conn, repo_root)

    def sweep(self) -> None:
        resolve.sweep(self._conn)

    def sweep_skip_dir_files(self, skip_dirs: frozenset[str]) -> None:
        resolve.sweep_skip_dir_files(self._conn, skip_dirs)

    @contextmanager
    def transaction(self) -> Iterator[GraphStore]:
        with store.transaction(self._conn):
            yield self


def open_reader(workspace: Path) -> GraphReader:
    """Open a read-only GraphReader on ``<workspace>/.agent-workspace/code.db``.

    ``GraphNotInitializedError`` / ``SchemaMismatchError`` propagate from store.
    """
    return GraphReader(store.read_only_connect(_db_path(workspace)))


def open_writer(workspace: Path, *, create: bool = False) -> GraphStore:
    """Open a read-write GraphStore on ``<workspace>/.agent-workspace/code.db``.

    ``create=True`` initializes a fresh graph schema at the workspace.
    """
    return GraphStore(store.connect(_db_path(workspace), create=create))

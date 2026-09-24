"""Tests for the GraphReader / GraphStore handle API and public re-exports."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import code_graph_io
import pytest
from code_graph_io import GraphReader, GraphStore, graphml, open_reader, open_writer, queries, resolve, upsert
from code_graph_io.paths import graph_dir
from code_graph_io.store import GraphNotInitializedError


class _FakeConn:
    """Stand-in connection: delegation tests never execute SQL."""


def test_open_reader_missing_db_raises(tmp_path: Path):
    with pytest.raises(GraphNotInitializedError):
        open_reader(graph_dir=graph_dir(tmp_path))  # no .agent-workspace/code.db


def test_reader_is_context_manager_and_closeable(seeded_workspace: Path):
    # context-manager form
    with open_reader(graph_dir=graph_dir(seeded_workspace)) as reader:
        assert isinstance(reader, GraphReader)
        assert isinstance(reader.list_packages(), list)  # smoke
    # explicit form
    reader = open_reader(graph_dir=graph_dir(seeded_workspace))
    try:
        reader.list_packages()
    finally:
        reader.close()


def test_writer_subclasses_reader(seeded_workspace: Path):
    with open_writer(graph_dir=graph_dir(seeded_workspace)) as store:
        assert isinstance(store, GraphStore)
        assert isinstance(store, GraphReader)


def test_describe_package_delegates(seeded_workspace: Path):
    with open_reader(graph_dir=graph_dir(seeded_workspace)) as reader:
        # any package known to the seeded graph; assert structural fields exist
        pkgs = reader.list_packages()
        assert pkgs, "seed graph should contain packages"
        desc = reader.describe_package(name=pkgs[0].name)
        assert desc is not None and desc.name == pkgs[0].name


def test_to_graphml_smoke(seeded_workspace: Path):
    with open_reader(graph_dir=graph_dir(seeded_workspace)) as reader:
        xml = reader.to_graphml()
        assert isinstance(xml, str)
        assert "graphml" in xml


def test_public_reexports_importable():
    for sym in (
        "GraphReader",
        "GraphStore",
        "open_reader",
        "open_writer",
        "NodeRecord",
        "PackageDescription",
        "AppDescription",
        "PathDescription",
        "RepoDescription",
        "EntryPointDescription",
        "SuiteDescription",
        "DependencyDescription",
        "BuiltinDescription",
        "AgentPluginDescription",
        "SymbolDescription",
        "ImportRecord",
        "ImporterRecord",
        "ExportRecord",
        "ExporterRecord",
        "CallRecord",
        "ChildNode",
        "MatchRecord",
        "GraphNotInitializedError",
        "SchemaMismatchError",
    ):
        assert hasattr(code_graph_io, sym), sym


# ---------------------------------------------------------------------------
# Delegation contract
#
# Every method on GraphReader/GraphStore is a thin forward to a module-internal
# function, passing `self._conn` as the first argument. That indirection is the
# whole point of the handle API (see CLAUDE.md, "Graph DB boundary"): callers
# never touch a connection. These tests pin the wiring — that each method
# reaches the *right* backend function and hands it the handle's own conn —
# rather than re-testing query behaviour, which the queries suite already owns.
# ---------------------------------------------------------------------------

# (handle method, backend module, backend attr, args, kwargs)
_READER_DELEGATIONS = [
    ("find", queries, "find", (), {"name": "n", "kind": "k", "in_package": "p"}),
    ("resolve_selector", queries, "resolve_selector", (), {"selector": "s", "in_package": "p"}),
    ("build_menu", queries, "build_menu", ("matches",), {}),
    ("containing_package", queries, "containing_package", (), {"path": "p"}),
    ("describe_symbol", queries, "describe_symbol", (), {"kind": "k", "name": "n"}),
    ("describe_package", queries, "describe_package", (), {"name": "n", "uri": "pkg:o/r/n"}),
    ("describe_app", queries, "describe_app", (), {"name": "n", "uri": "app:o/r/n"}),
    ("describe_path", queries, "describe_path", (), {"path": "p"}),
    ("describe_file", queries, "describe_file", (), {"uri": "file:o/r/p"}),
    ("describe_repository", queries, "describe_repository", (), {}),
    ("describe_entry_point", queries, "describe_entry_point", (), {"package_name": "p", "entry_name": "e"}),
    (
        "describe_test_suite",
        queries,
        "describe_test_suite",
        (),
        {"suite_name": "s", "uri": "test_suite:o/r/s"},
    ),
    ("describe_dependency", queries, "describe_dependency", (), {"uri": "dependency:o/r/pypi/n"}),
    ("describe_builtin", queries, "describe_builtin", (), {"language": "python", "module_name": "os"}),
    (
        "describe_agent_plugin",
        queries,
        "describe_agent_plugin",
        (),
        {"name": "n", "uri": "agent_plugin:o/r/n"},
    ),
    ("callers", queries, "callers", (), {"name": "n"}),
    ("callees", queries, "callees", (), {"name": "n"}),
    ("imports", queries, "imports", (), {"path": "p"}),
    ("imported_by", queries, "imported_by", (), {"path": "p"}),
    ("exports", queries, "exports", (), {"path": "p"}),
    ("exported_by", queries, "exported_by", (), {"name": "n"}),
    ("children_tree", queries, "children_tree", (), {"node": "nd", "depth": 2}),
    ("children_for", queries, "children_for", (), {"kind": "package"}),
    ("tests_for_package", queries, "tests_for_package", (), {"package_name": "p"}),
    ("entry_points_for_package", queries, "entry_points_for_package", (), {"package_name": "p"}),
    ("internal_dependencies_of", queries, "internal_dependencies_of", (), {"name": "n"}),
    ("resolve_entry_point", queries, "resolve_entry_point", ("raw",), {}),
    ("list_repositories", queries, "list_repositories", (), {}),
    ("list_packages", queries, "list_packages", (), {}),
    ("list_apps", queries, "list_apps", (), {}),
    ("list_entry_points", queries, "list_entry_points", (), {}),
    ("list_test_suites", queries, "list_test_suites", (), {}),
    ("list_dependencies", queries, "list_dependencies", (), {}),
    ("list_builtins", queries, "list_builtins", (), {}),
    ("list_agent_plugins", queries, "list_agent_plugins", (), {}),
    ("list_scripts", queries, "list_scripts", (), {}),
    ("to_graphml", graphml, "to_graphml", (), {}),
    ("metadata", queries, "metadata", ("key",), {}),
    ("node_count", queries, "node_count", (), {}),
    ("node_counts_by_kind", queries, "node_counts_by_kind", (), {}),
    ("edge_counts_by_kind", queries, "edge_counts_by_kind", (), {}),
    ("languages", queries, "languages", (), {}),
    ("file_paths", queries, "file_paths", (), {}),
    ("file_paths_in_package", queries, "file_paths_in_package", ("pkg",), {}),
    ("file_attrs", queries, "file_attrs", ("path",), {}),
    ("files_in_node", queries, "files_in_node", (7,), {}),
    ("symbol_names_under_files", queries, "symbol_names_under_files", ([1, 2],), {}),
    ("declared_entry_points", queries, "declared_entry_points", (), {}),
    ("node_exists", queries, "node_exists", (), {"kind": "k", "name": "n"}),
    ("package_for_file", queries, "package_for_file", (), {"path": "p"}),
    ("entity_by_name", queries, "entity_by_name", (), {"name": "n"}),
    ("package_or_app_by_dir", queries, "package_or_app_by_dir", (), {"path": "p"}),
    ("consumer_packages", queries, "consumer_packages", (), {"kind": "package"}),
]

_WRITER_DELEGATIONS = [
    ("upsert_records", upsert, "upsert_records", ("records",), {}),
    ("set_current_repo", upsert, "set_current_repo", ("repo:o/r",), {}),
    ("resolve_file_imports", resolve, "resolve_file_imports", (Path("/repo"),), {}),
    ("sweep", resolve, "sweep", (), {}),
    ("sweep_skip_dir_files", resolve, "sweep_skip_dir_files", (frozenset({"d"}),), {}),
]


def _assert_delegates(monkeypatch, handle, method, module, attr, args, kwargs):
    """Patch `module.attr`, call `handle.method(*args, **kwargs)`, assert wiring."""
    recorded = {}
    sentinel = object()

    def fake(conn, *a, **kw):
        recorded["conn"] = conn
        recorded["passed"] = list(a) + list(kw.values())
        return sentinel

    monkeypatch.setattr(module, attr, fake)
    result = getattr(handle, method)(*args, **kwargs)

    assert recorded["conn"] is handle._conn, f"{method} did not pass its own conn"
    for value in (*args, *kwargs.values()):
        assert value in recorded["passed"], f"{method} dropped {value!r} on the way to {attr}"
    return result, sentinel


@pytest.mark.parametrize(
    ("method", "module", "attr", "args", "kwargs"),
    _READER_DELEGATIONS,
    ids=[d[0] for d in _READER_DELEGATIONS],
)
def test_reader_method_delegates(monkeypatch, method, module, attr, args, kwargs):
    reader = GraphReader(_FakeConn())
    result, sentinel = _assert_delegates(monkeypatch, reader, method, module, attr, args, kwargs)
    assert result is sentinel, f"{method} did not return the backend's result"


@pytest.mark.parametrize(
    ("method", "module", "attr", "args", "kwargs"),
    _WRITER_DELEGATIONS,
    ids=[d[0] for d in _WRITER_DELEGATIONS],
)
def test_writer_method_delegates(monkeypatch, method, module, attr, args, kwargs):
    store_handle = GraphStore(_FakeConn())
    _assert_delegates(monkeypatch, store_handle, method, module, attr, args, kwargs)


def test_reader_delegation_table_covers_every_public_method():
    """The table above is exhaustive — a new handle method must be added to it.

    Without this, a method added to GraphReader silently goes untested; the
    delegation tests would still pass because they only iterate the table.
    """
    covered = {d[0] for d in _READER_DELEGATIONS} | {d[0] for d in _WRITER_DELEGATIONS}
    covered |= {"close", "dump_sql", "transaction"}  # exercised by their own tests
    public = {
        name
        for cls in (GraphReader, GraphStore)
        for name, attr in vars(cls).items()
        if not name.startswith("_") and callable(attr)
    }
    assert public - covered == set(), "handle methods missing from the delegation table"


def test_dump_sql_streams_from_the_connection(seeded_workspace: Path):
    with open_reader(graph_dir=graph_dir(seeded_workspace)) as reader:
        statements = list(reader.dump_sql())
    assert any("CREATE TABLE" in s for s in statements)


def test_transaction_yields_the_same_store(seeded_workspace: Path):
    with open_writer(graph_dir=graph_dir(seeded_workspace)) as store_handle, store_handle.transaction() as txn:
        assert txn is store_handle


# ---------------------------------------------------------------------------
# Cross-thread access
#
# The reader's sqlite3.Connection is opened on whichever thread calls
# open_reader(). Consumers such as graph_works_core's LangChain tool wrappers
# fall back to BaseTool.ainvoke() -> run_in_executor(None, self._run, ...),
# which runs the sync query on a threadpool worker thread that is NOT the
# thread that opened the connection. sqlite3's default check_same_thread=True
# makes that a hard error unless the reader is built to tolerate it; these
# tests pin that it does, and that concurrent cross-thread access is actually
# serialized rather than merely permitted one-at-a-time.
# ---------------------------------------------------------------------------


def test_reader_usable_from_a_different_thread(seeded_workspace: Path):
    """Open on this thread, query from another — mirrors run_in_executor()."""
    reader = open_reader(graph_dir=graph_dir(seeded_workspace))
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            packages = pool.submit(reader.list_packages).result()
        assert isinstance(packages, list)
        assert packages, "seed graph should contain packages"
    finally:
        reader.close()


def test_reader_serializes_concurrent_cross_thread_queries(seeded_workspace: Path):
    """Several threads hammering the same reader concurrently must not race.

    A raw sqlite3.Connection permits sequential use from a different thread
    once check_same_thread=False is set, but is not safe for *concurrent*
    multi-thread use. This simulates asyncio.gather() over several ainvoke()
    calls, each landing on its own executor thread at (close to) the same
    time, and asserts every call succeeds and returns the same, correct
    result — proving the reader actually serializes access.
    """
    reader = open_reader(graph_dir=graph_dir(seeded_workspace))
    try:
        expected = {p.name for p in reader.list_packages()}
        assert expected, "seed graph should contain packages"

        barrier = threading.Barrier(8)

        def query() -> set[str]:
            barrier.wait(timeout=5)
            return {p.name for p in reader.list_packages()}

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(query) for _ in range(8)]
            results = [f.result() for f in futures]

        assert len(results) == 8
        for result in results:
            assert result == expected
    finally:
        reader.close()

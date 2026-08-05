"""Tests for the GraphReader / GraphStore handle API and public re-exports."""

from __future__ import annotations

from pathlib import Path

import code_graph_io
import pytest
from code_graph_io import GraphReader, GraphStore, open_reader, open_writer
from code_graph_io.store import GraphNotInitializedError


def test_open_reader_missing_db_raises(tmp_path: Path):
    with pytest.raises(GraphNotInitializedError):
        open_reader(tmp_path)  # no .agent-workspace/code.db


def test_reader_is_context_manager_and_closeable(seeded_workspace: Path):
    # context-manager form
    with open_reader(seeded_workspace) as reader:
        assert isinstance(reader, GraphReader)
        assert isinstance(reader.list_packages(), list)  # smoke
    # explicit form
    reader = open_reader(seeded_workspace)
    try:
        reader.list_packages()
    finally:
        reader.close()


def test_writer_subclasses_reader(seeded_workspace: Path):
    with open_writer(seeded_workspace) as store:
        assert isinstance(store, GraphStore)
        assert isinstance(store, GraphReader)


def test_describe_package_delegates(seeded_workspace: Path):
    with open_reader(seeded_workspace) as reader:
        # any package known to the seeded graph; assert structural fields exist
        pkgs = reader.list_packages()
        assert pkgs, "seed graph should contain packages"
        desc = reader.describe_package(name=pkgs[0].name)
        assert desc is not None and desc.name == pkgs[0].name


def test_to_graphml_smoke(seeded_workspace: Path):
    with open_reader(seeded_workspace) as reader:
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

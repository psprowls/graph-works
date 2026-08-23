"""code-graph-io: SQLite-backed code graph for the agent-workspace ecosystem.

The package owns the SQLite store, manifest scanning, and read-only queries.
The public access surface is the GraphReader / GraphStore handle pair and the
open_reader / open_writer openers re-exported below.
"""

__version__ = "0.3.0"

from code_graph_io.handle import GraphReader, GraphStore, open_reader, open_writer
from code_graph_io.queries import _VALID_KINDS as VALID_KINDS
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
    FileDescription,
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
from code_graph_io.schema import SCHEMA_VERSION
from code_graph_io.source_meta import extension_languages
from code_graph_io.store import GraphNotInitializedError, SchemaMismatchError

__all__ = [
    "SCHEMA_VERSION",
    "VALID_KINDS",
    "AgentPluginDescription",
    "AppDescription",
    "BuiltinDescription",
    "CallRecord",
    "ChildNode",
    "DependencyDescription",
    "EntryPointDescription",
    "ExportRecord",
    "ExporterRecord",
    "FileDescription",
    "GraphNotInitializedError",
    "GraphReader",
    "GraphStore",
    "ImportRecord",
    "ImporterRecord",
    "MatchRecord",
    "NodeRecord",
    "PackageDescription",
    "PathDescription",
    "RepoDescription",
    "SchemaMismatchError",
    "SuiteDescription",
    "SymbolDescription",
    "extension_languages",
    "open_reader",
    "open_writer",
]

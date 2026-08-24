"""code-parser — tree-sitter-backed source parsing for the graph-works ecosystem."""

from code_graph_io.parser.errors import UnsupportedLanguageError
from code_graph_io.parser.parse import parse_bytes, parse_file
from code_graph_io.parser.projections.graph import (
    GraphEdge,
    GraphNode,
    GraphRecords,
    NodeKey,
    to_graph_records,
)
from code_graph_io.parser.tree import Reference, SourceNode, Span

__all__ = [
    "GraphEdge",
    "GraphNode",
    "GraphRecords",
    "NodeKey",
    "Reference",
    "SourceNode",
    "Span",
    "UnsupportedLanguageError",
    "parse_bytes",
    "parse_file",
    "to_graph_records",
]

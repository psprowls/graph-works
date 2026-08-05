"""code-parser — tree-sitter-backed source parsing for the agent-workspace ecosystem."""

from code_parser.errors import UnsupportedLanguageError
from code_parser.parse import parse_bytes, parse_file
from code_parser.projections.graph import (
    GraphEdge,
    GraphNode,
    GraphRecords,
    NodeKey,
    to_graph_records,
)
from code_parser.tree import Reference, SourceNode, Span

__version__ = "0.1.0"

__all__ = [
    "GraphEdge",
    "GraphNode",
    "GraphRecords",
    "NodeKey",
    "Reference",
    "SourceNode",
    "Span",
    "UnsupportedLanguageError",
    "__version__",
    "parse_bytes",
    "parse_file",
    "to_graph_records",
]

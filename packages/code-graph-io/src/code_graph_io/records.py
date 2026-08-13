"""Helpers for building immutable parser graph records from mutable emitters."""

from __future__ import annotations

from collections.abc import Iterable

from code_graph_io.parser.projections.graph import GraphEdge, GraphNode, GraphRecords, NodeKey

__all__ = ["GraphEdge", "GraphNode", "GraphRecords", "NodeKey", "as_graph_records"]


def as_graph_records(
    nodes: Iterable[GraphNode] = (),
    edges: Iterable[GraphEdge] = (),
) -> GraphRecords:
    """Return GraphRecords with the tuple boundary expected by the parser."""
    return GraphRecords(nodes=tuple(nodes), edges=tuple(edges))

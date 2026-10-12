"""The code-read vertical: a context window of one declared repository's file.

Layer 2 -- independent of every other vertical; imports `code_graph_io`, `graph` and
`workspace` only. It shares `workspace.repo_files` with `wiki_page`, whose
citations it serves.
"""

from __future__ import annotations

from gw_wiki.code_read.commands import (
    CONTEXT,
    MAX_SPAN,
    CodeExcerpt,
    ExcerptRefusal,
    language_for,
    run_code_excerpt,
)
from gw_wiki.code_read.neighborhood import (
    GraphEdge,
    GraphNode,
    Neighborhood,
    NeighborhoodRefusal,
    run_code_graph_neighborhood,
)
from gw_wiki.code_read.tree import (
    CodeGraphSearch,
    CodeGraphTree,
    CodeTreeNode,
    SearchHit,
    TreeRefusal,
    run_code_graph_search,
    run_code_graph_tree,
)

__all__ = [
    "CONTEXT",
    "MAX_SPAN",
    "CodeExcerpt",
    "CodeGraphSearch",
    "CodeGraphTree",
    "CodeTreeNode",
    "ExcerptRefusal",
    "GraphEdge",
    "GraphNode",
    "Neighborhood",
    "NeighborhoodRefusal",
    "SearchHit",
    "TreeRefusal",
    "language_for",
    "run_code_excerpt",
    "run_code_graph_neighborhood",
    "run_code_graph_search",
    "run_code_graph_tree",
]

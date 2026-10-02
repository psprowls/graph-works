"""Samples for `graph_works_wire.code` projections."""

from __future__ import annotations

from collections.abc import Callable

from graph_works_core.code_read import (
    CodeExcerpt,
    CodeGraphSearch,
    CodeGraphTree,
    CodeTreeNode,
    GraphEdge,
    GraphNode,
    Neighborhood,
    SearchHit,
)
from graph_works_wire import code

CODE: dict[str, tuple[Callable[[], object], ...]] = {
    "code.excerpt_payload": (
        lambda: code.excerpt_payload(CodeExcerpt("gw", "a.py", 3, 3, 1, 8, 10, "python", ("x", "y"), None)),
        lambda: code.excerpt_payload(CodeExcerpt("gw", "a.py", 20, 20, None, None, 10, None, (), "out-of-range")),
        lambda: code.excerpt_payload(
            CodeExcerpt("nope", "a.py", 1, 1, None, None, None, None, (), "unknown-repository")
        ),
    ),
    "code.code_graph_tree_payload": (
        lambda: code.code_graph_tree_payload(
            CodeGraphTree(
                "gw",
                (
                    CodeTreeNode("code-graph/gw", "gw", "Repository", None, None, False),
                    CodeTreeNode(
                        "code-graph/gw/entities/packages/core",
                        "core",
                        "Package",
                        "pkg:a/gw/core",
                        "code-graph/gw",
                        False,
                    ),
                    CodeTreeNode("code-graph/gw/file-system/src/index", "src", None, None, "code-graph/gw", True),
                ),
                None,
            )
        ),
        lambda: code.code_graph_tree_payload(CodeGraphTree("nope", (), "unknown-repository")),
    ),
    "code.code_graph_search_payload": (
        lambda: code.code_graph_search_payload(
            CodeGraphSearch(
                "core",
                "gw",
                (SearchHit("code-graph/gw/entities/packages/core", "core", "Package", "pkg:a/gw/core", "d", "gw"),),
                True,
                None,
            )
        ),
        lambda: code.code_graph_search_payload(CodeGraphSearch("x", "nope", (), False, "unknown-repository")),
    ),
    "code.code_graph_neighborhood_payload": (
        lambda: code.code_graph_neighborhood_payload(
            Neighborhood(
                "code-graph/gw/entities/packages/core",
                "pkg:a/gw/core",
                1,
                (
                    GraphNode("pkg:a/gw/core", "code-graph/gw/entities/packages/core", 0),
                    GraphNode("pkg:a/gw/io", None, 1),
                ),
                (GraphEdge("pkg:a/gw/core", "pkg:a/gw/io", "depends-on"),),
                False,
                None,
            )
        ),
        lambda: code.code_graph_neighborhood_payload(Neighborhood("nope/x", None, 1, (), (), False, "unknown-page")),
    ),
}

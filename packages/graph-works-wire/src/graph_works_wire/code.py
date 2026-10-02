"""Plain-data projections for code reads."""

from __future__ import annotations

from graph_works_core.code_read import CodeExcerpt, CodeGraphSearch, CodeGraphTree, Neighborhood


def excerpt_payload(result: CodeExcerpt) -> dict[str, object]:
    """`/v1/code/excerpt`: the served window, context included, or a refusal."""
    return {
        "repo": result.repo,
        "path": result.path,
        "start": result.start,
        "end": result.end,
        "first": result.first,
        "last": result.last,
        "total_lines": result.total_lines,
        "language": result.language,
        "lines": list(result.lines),
        "refusal": result.refusal,
    }


def code_graph_tree_payload(tree: CodeGraphTree) -> dict[str, object]:
    """`/v1/code-graph/tree`: a repository's pages as a flat parent-linked list, or a refusal."""
    return {
        "repo": tree.repo,
        "nodes": [
            {
                "id": node.id,
                "title": node.title,
                "type": node.type,
                "resource": node.resource,
                "parent": node.parent,
                "is_index": node.is_index,
            }
            for node in tree.nodes
        ],
        "refusal": tree.refusal,
    }


def code_graph_search_payload(result: CodeGraphSearch) -> dict[str, object]:
    """`/v1/code-graph/search`: ranked hits, whether more matched, or a refusal."""
    return {
        "q": result.q,
        "repo": result.repo,
        "hits": [
            {
                "id": hit.id,
                "title": hit.title,
                "type": hit.type,
                "resource": hit.resource,
                "description": hit.description,
                "repo": hit.repo,
            }
            for hit in result.hits
        ],
        "truncated": result.truncated,
        "refusal": result.refusal,
    }


def code_graph_neighborhood_payload(result: Neighborhood) -> dict[str, object]:
    """`/v1/code-graph/neighborhood`: depends-on nodes and edges around a page's URI, or a refusal."""
    return {
        "id": result.id,
        "uri": result.uri,
        "depth": result.depth,
        "nodes": [{"uri": node.uri, "page_id": node.page_id, "depth": node.depth} for node in result.nodes],
        "edges": [{"source": edge.source, "target": edge.target, "kind": edge.kind} for edge in result.edges],
        "truncated": result.truncated,
        "refusal": result.refusal,
    }

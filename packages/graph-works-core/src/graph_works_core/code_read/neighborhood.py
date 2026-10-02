"""The depends-on neighbourhood of one scanned code-graph page, read from the code graph.

Refusals are results: an unknown page, a page with no `resource:`, a missing
graph, or a URI the graph does not hold come back as a complete value with
`refusal` set. Only a depth outside 1..3 raises, because it is a usage error.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from code_graph_io import GraphNotInitializedError, GraphReader, SchemaMismatchError, open_reader

from graph_works_core.graph.commands import graph_target
from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.layout import WorkspaceLayout

#: `not-in-graph`: the page names a URI the code graph does not hold.
NeighborhoodRefusal = Literal["unknown-page", "no-resource", "no-graph", "not-in-graph"]

MIN_DEPTH = 1
MAX_DEPTH = 3
_CAP = 200


@dataclass(frozen=True, slots=True)
class GraphNode:
    """One entity or file in the neighbourhood; `page_id` is its code-graph page when the bundle has one."""

    uri: str
    page_id: str | None
    depth: int


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """`source` depends on `target`."""

    source: str
    target: str
    kind: Literal["depends-on"]


@dataclass(frozen=True, slots=True)
class Neighborhood:
    """Nodes within `depth` hops of the page's URI, in both directions, and the edges between them."""

    id: str
    uri: str | None
    depth: int
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    truncated: bool
    refusal: NeighborhoodRefusal | None


def _refused(page_id: str, depth: int, refusal: NeighborhoodRefusal, uri: str | None = None) -> Neighborhood:
    return Neighborhood(page_id, uri, depth, (), (), False, refusal)


class _Adjacency:
    """Dependent -> dependency edges of the graph, loaded once per call."""

    def __init__(self, reader: GraphReader) -> None:
        self._reader = reader
        self._out: dict[str, set[str]] = defaultdict(set)
        self._in: dict[str, set[str]] = defaultdict(set)
        self._entities: set[str] = set()
        entities = (*reader.list_packages(), *reader.list_apps(), *reader.list_agent_plugins())
        for record in (*entities, *reader.list_test_suites()):
            uri = record.attrs.get("uri")
            if not isinstance(uri, str):
                continue
            self._entities.add(uri)
            for target in (*reader.internal_dependency_uris_of(uri=uri), *reader.external_dependencies_of(uri=uri)):
                self._out[uri].add(target)
                self._in[target].add(uri)
        self._files = frozenset(reader.file_uris())

    def knows(self, uri: str) -> bool:
        return uri in self._entities or uri in self._in or uri in self._files

    def edges(self, uri: str) -> set[tuple[str, str]]:
        if uri in self._files:
            return self._file_edges(uri)
        return {(uri, target) for target in self._out.get(uri, ())} | {
            (source, uri) for source in self._in.get(uri, ())
        }

    def _file_edges(self, uri: str) -> set[tuple[str, str]]:
        description = self._reader.describe_file(uri=uri)
        if description is None:
            return set()
        # A file URI is `file:<org>/<repo>/<path>`: the prefix keeps every neighbour in the same repository.
        prefix = uri[: len(uri) - len(description.path)]
        found: set[tuple[str, str]] = set()
        # `imports(path=)` and `imported_by(path=)` match on path alone, so they span repositories that share a
        # path. `describe_file(uri=).imports` is scoped by node id: outgoing edges come from it, and a path-matched
        # importer counts only when its own uri-scoped imports include this file.
        for record in description.imports:
            if record.path is not None and prefix + record.path in self._files:
                found.add((uri, prefix + record.path))
        for importer in self._reader.imported_by(path=description.path, depth=1):
            candidate = prefix + importer.path
            if candidate not in self._files:
                continue
            candidate_description = self._reader.describe_file(uri=candidate)
            if candidate_description is not None and any(
                record.path == description.path for record in candidate_description.imports
            ):
                found.add((candidate, uri))
        found.discard((uri, uri))
        return found


def _pages_by_resource(layout: WorkspaceLayout) -> dict[str, str]:
    pages: dict[str, str] = {}
    for concept_id, document in sorted(load_workspace_bundle(layout).concepts.items(), reverse=True):
        resource = document.fm_data().get("resource")
        if isinstance(resource, str) and resource:
            pages[resource] = concept_id
    return pages


def run_code_graph_neighborhood(layout: WorkspaceLayout, page_id: str, depth: int) -> Neighborhood:
    """Breadth-first walk over both directions of depends-on from the page's URI, capped at 200 nodes."""
    if not MIN_DEPTH <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be between {MIN_DEPTH} and {MAX_DEPTH} (got {depth})")
    bundle = load_workspace_bundle(layout)
    document = bundle.concept(page_id)
    if document is None:
        return _refused(page_id, depth, "unknown-page")
    uri = document.fm_data().get("resource")
    if not isinstance(uri, str) or not uri:
        return _refused(page_id, depth, "no-resource")
    try:
        reader = open_reader(graph_dir=graph_target(layout).graph_dir)
    except (GraphNotInitializedError, SchemaMismatchError):
        return _refused(page_id, depth, "no-graph", uri)
    try:
        if reader.node_count() == 0:
            return _refused(page_id, depth, "no-graph", uri)
        adjacency = _Adjacency(reader)
        if not adjacency.knows(uri):
            return _refused(page_id, depth, "not-in-graph", uri)
        seen: dict[str, int] = {uri: 0}
        edges: set[tuple[str, str]] = set()
        frontier = [uri]
        truncated = False
        for level in range(1, depth + 1):
            following: list[str] = []
            for current in frontier:
                for source, target in sorted(adjacency.edges(current)):
                    edges.add((source, target))
                    other = target if source == current else source
                    if other in seen:
                        continue
                    if len(seen) >= _CAP:
                        truncated = True
                        continue
                    seen[other] = level
                    following.append(other)
            frontier = sorted(following)
    finally:
        reader.close()
    pages = _pages_by_resource(layout)
    nodes = tuple(GraphNode(u, pages.get(u), d) for u, d in sorted(seen.items(), key=lambda item: (item[1], item[0])))
    kept = tuple(GraphEdge(s, t, "depends-on") for s, t in sorted(edges) if s in seen and t in seen)
    return Neighborhood(page_id, uri, depth, nodes, kept, truncated, None)

"""`compute_stats` -- the seven-key structural surface over a bundle's link graph.

Ports `graph_analyzer.py`'s output shape
(`wiki/concepts/graph-works-plugin-cli-contract.md`) onto OKF v0.2's
markdown-link bundles rather than the legacy `[[wikilink]]` vault: wikilink
parsing and `depends_on:`-as-edge are both dropped, since `okf_io.Frontmatter`
has no typed field the latter could read from.

Pure function, no I/O: the caller resolves a workspace and loads the bundle
(`okf_io.load_bundle`) itself, matching `okf_io.build_link_graph`'s own
signature.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from okf_io import Bundle, LinkGraph, build_link_graph


@dataclass(frozen=True, slots=True)
class HubEntry:
    page: str
    degree: int


@dataclass(frozen=True, slots=True)
class WikiStats:
    total_pages: int
    total_edges: int
    component_count: int
    top_outbound_hubs: tuple[HubEntry, ...]
    top_inbound_hubs: tuple[HubEntry, ...]
    orphans: tuple[str, ...]
    sinks: tuple[str, ...]


def _out_edges(bundle: Bundle, graph: LinkGraph) -> dict[str, tuple[str, ...]]:
    """Edges that actually count, bucketed by source.

    The identical filter `okf_io.links.build` applies to populate
    `LinkGraph.backlinks`, just keyed by source instead of target.
    `LinkGraph.out` is not reusable here: it holds every parsed link --
    external, broken, and image links included -- and using it would
    silently overcount both `total_edges` and outbound hub degree.
    """
    edges: defaultdict[str, set[str]] = defaultdict(set)
    for link in graph.links:
        if link.external:
            continue
        if link.target is None or not bundle.has_member(link.target):
            continue
        if not link.image and link.target.endswith(".md"):
            target_id = link.target[: -len(".md")]
            if target_id in bundle.concepts:
                edges[link.source].add(target_id)
    return {source: tuple(sorted(targets)) for source, targets in edges.items()}


def _component_count(
    nodes: Sequence[str],
    out_edges: Mapping[str, tuple[str, ...]],
    backlinks: Mapping[str, tuple[str, ...]],
) -> int:
    """Undirected connected components over `nodes`, flood-filled via BFS.

    No membership filter on `out_edges`/`backlinks` targets: both are
    already restricted to `bundle.concepts` (`_out_edges`'s own filter, and
    `okf_io.links.build`'s identical one for `backlinks`), which is exactly
    `nodes`, so every neighbour is already a key of `adjacency`.
    """
    adjacency: dict[str, set[str]] = {n: set() for n in nodes}
    for n in nodes:
        adjacency[n].update(out_edges.get(n, ()))
        adjacency[n].update(backlinks.get(n, ()))

    seen: set[str] = set()
    count = 0
    for start in nodes:
        if start in seen:
            continue
        count += 1
        seen.add(start)
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
    return count


def _top_hubs(nodes: Sequence[str], degree_of: Callable[[str], int], top: int) -> tuple[HubEntry, ...]:
    """Degree descending, page id ascending as tiebreak -- deterministic,
    unlike the legacy analyzer's unkeyed `set` sort.
    """
    ranked = sorted(nodes, key=lambda n: (-degree_of(n), n))
    return tuple(HubEntry(page=n, degree=degree_of(n)) for n in ranked[:top])


def compute_stats(bundle: Bundle, *, top: int = 10) -> WikiStats:
    graph = build_link_graph(bundle)
    nodes = sorted(bundle.concepts)
    out_edges = _out_edges(bundle, graph)

    orphans = tuple(n for n in nodes if not graph.backlinks.get(n))
    sinks = tuple(n for n in nodes if not out_edges.get(n))

    return WikiStats(
        total_pages=len(nodes),
        total_edges=sum(len(v) for v in out_edges.values()),
        component_count=_component_count(nodes, out_edges, graph.backlinks),
        top_outbound_hubs=_top_hubs(nodes, lambda n: len(out_edges.get(n, ())), top),
        top_inbound_hubs=_top_hubs(nodes, lambda n: len(graph.backlinks.get(n, ())), top),
        orphans=orphans,
        sinks=sinks,
    )

"""Transitive-dependent rank over one orchestrate run's dependency graph.

The planner admits candidates greedily, so the order it considers them in is
the whole of its priority policy. An item that many others wait on -- directly
or through a chain -- should be considered before a sibling nobody waits on
(epic `auto-drive-scheduling-correctness` D-003). This module computes that
count and nothing else: it gates nothing, and every readiness, hold, claim,
capacity and placement check stays where it was.

The graph is pre-eligibility and subtree-scoped. Its nodes are the run root
and every active nonterminal descendant, including blocked, held and live
ones, because the dependents that make an upstream important are exactly the
ones routing has already removed from the candidate list. Its edges are the
nodes' own resolved `depends_on` entries whose target is also a node; unknown,
terminal, archived and out-of-run targets contribute nothing and bridge
nothing. Containment and repository identity are not edges.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from work_tracker_okf.hierarchy import active_nonterminal_descendants
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import TERMINAL_STATUSES


def _run_nodes(items: Sequence[WorkItem], root: str) -> frozenset[str]:
    root_item = next((item for item in items if item.path == root), None)
    if root_item is None:
        return frozenset()
    nodes = set(active_nonterminal_descendants(items, root))
    if not root_item.archived and root_item.work_status not in TERMINAL_STATUSES:
        nodes.add(root)
    return frozenset(nodes)


def dependent_counts(items: Sequence[WorkItem], root: str) -> Mapping[str, int]:
    """`path -> number of distinct run nodes that transitively depend on it`.

    Every run node has an entry (zero when nothing waits on it); a path that is
    not a run node has none. A dependent reachable along several paths, or
    named by several phase edges, counts once; an item never counts itself.
    The walk is iterative with a per-origin visited set, so self-edges, cycles
    and chains deeper than the recursion limit terminate. Worst case is
    O(V * (V + E)) -- fine at work-tree sizes; optimize only on measured need,
    and never by memoizing partial reachability across a cycle.
    """
    nodes = _run_nodes(items, root)
    dependents: dict[str, set[str]] = {path: set() for path in nodes}
    for item in items:
        if item.path not in nodes:
            continue
        for edge in item.dependency_edges:
            if edge.path in nodes and edge.path != item.path:
                dependents[edge.path].add(item.path)
    counts: dict[str, int] = {}
    for origin in sorted(nodes):
        seen = {origin}
        pending = list(dependents[origin])
        while pending:
            path = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            pending.extend(dependents[path])
        counts[origin] = len(seen) - 1
    return MappingProxyType(counts)

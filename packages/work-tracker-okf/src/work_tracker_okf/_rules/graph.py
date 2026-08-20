"""Rules for `parent`, `depends_on` and the derived `children` key.

Every function takes the **whole** item set, archived included. `work-io` needed
a second loader and an `archived_items=` parameter for that; `load_items`
returns both and `WorkItem.archived` tells them apart, so the parameter goes
away.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import pairwise

from okf_io import Finding, Rule, RuleContext, Severity

from work_tracker_okf._rules._common import LaneConfig, active, items
from work_tracker_okf.children import plan_children_sync
from work_tracker_okf.dependencies import PHASE_ORDER
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import PARENT_TYPES, TERMINAL_STATUSES

CODES: tuple[str, ...] = (
    "graph.parent-missing",
    "graph.parent-type-invalid",
    "graph.depends-on-missing",
    "graph.depends-on-not-sibling",
    "graph.depends-on-invalid",
    "graph.parent-cycle",
    "graph.depends-on-cycle",
    "graph.epic-without-children",
    "graph.children-stale",
)

_SPEC = "work_tracker_okf._rules.graph"

#: The phases by which an epic is expected to have decomposed. work-io's rule 29
#: gate, unchanged.
_DECOMPOSED_PHASES = frozenset({"execute", "finish", "done"})

_PHASE_CHAIN = PHASE_ORDER[:-1]
_RESOLVED = "resolved"


def _entry_node(slug: str, phase: str) -> str:
    return f"{slug}#{phase}:entry"


def _completion_node(slug: str, phase: str) -> str:
    return f"{slug}#{phase}:complete"


def _resolved_node(slug: str) -> str:
    return f"{slug}#{_RESOLVED}"


def _finding(code: str, severity: Severity, item: WorkItem, message: str) -> Finding:
    return Finding(code=code, severity=severity, message=message, spec=_SPEC, path=item.path, line=None)


def _dependency_issue_message(issue_code: str, detail: str) -> str:
    label = "missing slug" if issue_code == "empty-slug" else issue_code.replace("-", " ")
    return f"`depends_on` has {label}: {detail}"


def references(ctx: RuleContext) -> Iterable[Finding]:
    """24-27: what `parent` and `depends_on` point at.

    Resolution runs over the **whole** item set: a reference to an archived item
    is valid, and reporting it as missing is the bug work-io needed a second
    loader to avoid. Only active items are *reported* on.

    `parent-kind-invalid` became `parent-type-invalid`: `kind` became `type` in
    the port, and a code naming a key that no longer exists is a code nobody can
    act on.
    """
    everything = items(ctx)
    by_slug = {item.slug: item for item in everything}
    for item in everything:
        if item.archived:
            continue
        if item.parent is not None:
            parent = by_slug.get(item.parent)
            if parent is None:
                yield _finding("graph.parent-missing", "error", item, f"`parent` {item.parent!r} names no work item")
            elif parent.type not in PARENT_TYPES:
                yield _finding(
                    "graph.parent-type-invalid",
                    "error",
                    item,
                    f"`parent` {item.parent!r} resolves but its `type` is {parent.type!r}, "
                    f"not one of {sorted(PARENT_TYPES)}",
                )
        for issue in item.dependency_issues:
            yield _finding(
                "graph.depends-on-invalid",
                "error",
                item,
                _dependency_issue_message(issue.code, issue.detail),
            )
        for edge in item.depends_on:
            if edge.slug == item.parent or edge.slug == item.slug:
                yield _finding(
                    "graph.depends-on-invalid",
                    "error",
                    item,
                    f"`depends_on` {edge.slug!r} names its parent or itself",
                )
            dependency = by_slug.get(edge.slug)
            if dependency is None:
                yield _finding(
                    "graph.depends-on-missing", "error", item, f"`depends_on` {edge.slug!r} names no work item"
                )
            elif item.parent is not None and dependency.parent != item.parent:
                yield _finding(
                    "graph.depends-on-not-sibling",
                    "warn",
                    item,
                    f"`depends_on` {edge.slug!r} has `parent` {dependency.parent!r}, not this item's {item.parent!r}",
                )


def _cycle_nodes(graph: dict[str, list[str]]) -> list[str]:
    """The sorted nodes participating in any cycle.

    Three-colour DFS, ported from `work_io.lifecycle_lint._cycle_nodes`: WHITE
    unvisited, GRAY on the current path, BLACK finished. Edges leaving the graph
    are ignored -- a dangling reference is `graph.depends-on-missing`'s finding,
    not a cycle.
    """
    white, gray, black = 0, 1, 2
    colour = dict.fromkeys(graph, white)
    in_cycle: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> None:
        colour[node] = gray
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt not in graph:
                continue
            if colour[nxt] == gray:
                in_cycle.update(stack[stack.index(nxt) :])
            elif colour[nxt] == white:
                visit(nxt)
        stack.pop()
        colour[node] = black

    for node in graph:
        if colour[node] == white:
            visit(node)
    return sorted(in_cycle)


def _phase_dependency_graph(work_items: Sequence[WorkItem]) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    for item in work_items:
        for phase in _PHASE_CHAIN:
            graph[_entry_node(item.slug, phase)] = []
            graph[_completion_node(item.slug, phase)] = []
        graph[_resolved_node(item.slug)] = []

    by_slug = {item.slug: item for item in work_items}
    for item in work_items:
        for phase in _PHASE_CHAIN:
            graph[_entry_node(item.slug, phase)].append(_completion_node(item.slug, phase))
        for current, following in pairwise(_PHASE_CHAIN):
            graph[_completion_node(item.slug, current)].append(_entry_node(item.slug, following))
        graph[_completion_node(item.slug, _PHASE_CHAIN[-1])].append(_resolved_node(item.slug))

    for item in work_items:
        for edge in item.depends_on:
            dependency = by_slug.get(edge.slug)
            if dependency is None or dependency.workflow_status in TERMINAL_STATUSES or edge.blocks not in _PHASE_CHAIN:
                continue
            if edge.needs == _RESOLVED:
                source = _resolved_node(edge.slug)
            elif edge.needs in _PHASE_CHAIN:
                source = _completion_node(edge.slug, edge.needs)
            else:
                continue
            graph[source].append(_entry_node(item.slug, edge.blocks))
    return graph


def cycles(ctx: RuleContext) -> Iterable[Finding]:
    """28, 30: a `depends_on` or `parent` chain that closes on itself.

    Both graphs are built from **active** items only. An archived item is
    terminal and frozen, and a cycle it participates in is not something anyone
    is being asked to break -- which is also work-io's behaviour, where archived
    items were excluded from the dependency graph outright.
    """
    active_items = tuple(active(ctx))
    by_slug = {item.slug: item for item in active_items}
    parents = {item.slug: ([item.parent] if item.parent is not None else []) for item in active_items}
    dependency_cycle_slugs = {node.partition("#")[0] for node in _cycle_nodes(_phase_dependency_graph(active_items))}
    for slug in sorted(dependency_cycle_slugs):
        yield _finding("graph.depends-on-cycle", "error", by_slug[slug], "`depends_on` participates in a cycle")
    for slug in _cycle_nodes(parents):
        yield _finding("graph.parent-cycle", "error", by_slug[slug], "`parent` chain participates in a cycle")


def derived(ctx: RuleContext) -> Iterable[Finding]:
    """29, 31: two facts about a parent's own children.

    `children-stale` does **not** re-derive the comparison. `load_items` fills
    `WorkItem.children` with the derived value and discards what the page
    authored, so the projection alone cannot see drift --
    `children.plan_children_sync` already re-reads the authored list off the
    document and returns only drifted parents (C3-C). This is the third consumer
    of one comparison rather than a second copy of it, and it is why this rule
    reports `sync.path` rather than building its own.
    """
    everything = items(ctx)
    for item in everything:
        if item.archived or item.type != "Epic":
            continue
        if item.phase in _DECOMPOSED_PHASES and not item.children:
            yield _finding(
                "graph.epic-without-children", "warn", item, f"`type: Epic` at `phase: {item.phase}` has no children"
            )
    for sync in plan_children_sync(ctx.bundle, everything):
        yield Finding(
            code="graph.children-stale",
            severity="warn",
            message=(
                f"authored `children` {list(sync.before)} disagrees with the derived {list(sync.after)}; "
                "run the children sync"
            ),
            spec=_SPEC,
            path=sync.path,
            line=None,
        )


def rules(config: LaneConfig) -> tuple[Rule, ...]:
    """Uniform with every other topic, including the two that inject nothing (C5-E)."""
    del config  # this topic injects nothing
    return (references, cycles, derived)

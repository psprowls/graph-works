"""Rules for path-keyed dependencies and decomposition."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import pairwise

from okf_io import Finding, Rule, RuleContext, Severity

from work_tracker_okf._rules._common import LaneConfig, active, items
from work_tracker_okf.dependencies import PHASE_ORDER
from work_tracker_okf.graph import cycle_nodes
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

CODES: tuple[str, ...] = (
    "graph.depends-on-missing",
    "graph.depends-on-not-sibling",
    "graph.depends-on-invalid",
    "graph.depends-on-cycle",
    "graph.epic-without-children",
)

_SPEC = "work_tracker_okf._rules.graph"
_DECOMPOSED_PHASES = frozenset({"execute", "finish", "done"})
_PHASE_CHAIN = PHASE_ORDER[:-1]
_RESOLVED = "resolved"


def _entry_node(path: str, phase: str) -> str:
    return f"{path}#{phase}:entry"


def _completion_node(path: str, phase: str) -> str:
    return f"{path}#{phase}:complete"


def _resolved_node(path: str) -> str:
    return f"{path}#{_RESOLVED}"


def _finding(code: str, severity: Severity, item: WorkItem, message: str) -> Finding:
    return Finding(code=code, severity=severity, message=message, spec=_SPEC, path=item.page_path, line=None)


def _dependency_issue_message(issue_code: str, detail: str) -> str:
    label = "missing path" if issue_code == "empty-path" else issue_code.replace("-", " ")
    return f"`depends_on` has {label}: {detail}"


def references(ctx: RuleContext) -> Iterable[Finding]:
    """Validate structured dependency targets against permanent item paths."""
    everything = items(ctx)
    by_path = {item.path: item for item in everything}
    for item in active(ctx):
        for issue in item.dependency_issues:
            yield _finding(
                "graph.depends-on-invalid", "error", item, _dependency_issue_message(issue.code, issue.detail)
            )
        for edge in item.dependency_edges:
            if edge.path == item.path or edge.path in item.ancestor_paths:
                yield _finding(
                    "graph.depends-on-invalid",
                    "error",
                    item,
                    f"`depends_on` {edge.path!r} names itself or a containing parent",
                )
            dependency = by_path.get(edge.path)
            if dependency is None:
                yield _finding(
                    "graph.depends-on-missing", "error", item, f"`depends_on` {edge.path!r} names no work item"
                )
            elif item.parent_path is not None and dependency.parent_path != item.parent_path:
                yield _finding(
                    "graph.depends-on-not-sibling",
                    "warn",
                    item,
                    f"`depends_on` {edge.path!r} has parent {dependency.parent_path!r}, not {item.parent_path!r}",
                )


def _phase_dependency_graph(work_items: Sequence[WorkItem]) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    for item in work_items:
        for phase in _PHASE_CHAIN:
            graph[_entry_node(item.path, phase)] = []
            graph[_completion_node(item.path, phase)] = []
        graph[_resolved_node(item.path)] = []

    by_path = {item.path: item for item in work_items}
    for item in work_items:
        for phase in _PHASE_CHAIN:
            graph[_entry_node(item.path, phase)].append(_completion_node(item.path, phase))
        for current, following in pairwise(_PHASE_CHAIN):
            graph[_completion_node(item.path, current)].append(_entry_node(item.path, following))
        graph[_completion_node(item.path, _PHASE_CHAIN[-1])].append(_resolved_node(item.path))

    for item in work_items:
        for edge in item.dependency_edges:
            dependency = by_path.get(edge.path)
            if dependency is None or dependency.work_status in TERMINAL_STATUSES or edge.blocks not in _PHASE_CHAIN:
                continue
            if edge.needs == _RESOLVED:
                source = _resolved_node(edge.path)
            elif edge.needs in _PHASE_CHAIN:
                source = _completion_node(edge.path, edge.needs)
            else:
                continue
            graph[source].append(_entry_node(item.path, edge.blocks))
    return graph


def cycles(ctx: RuleContext) -> Iterable[Finding]:
    """Report dependency cycles through the shared iterative graph primitive."""
    active_items = tuple(active(ctx))
    by_path = {item.path: item for item in active_items}
    paths = {node.partition("#")[0] for node in cycle_nodes(_phase_dependency_graph(active_items))}
    for path in sorted(paths):
        yield _finding("graph.depends-on-cycle", "error", by_path[path], "`depends_on` participates in a cycle")


def derived(ctx: RuleContext) -> Iterable[Finding]:
    """Warn when an executing epic has not yet acquired a direct child."""
    for item in active(ctx):
        if item.type != "Epic" or item.phase not in _DECOMPOSED_PHASES:
            continue
        if item.child_paths:
            continue
        yield _finding(
            "graph.epic-without-children", "warn", item, f"`type: Epic` at `phase: {item.phase}` has no children"
        )


def rules(config: LaneConfig) -> tuple[Rule, ...]:
    del config
    return (references, cycles, derived)

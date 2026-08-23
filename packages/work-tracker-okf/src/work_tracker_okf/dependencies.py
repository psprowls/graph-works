"""Structured, path-keyed dependency edges and their gate predicates."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from work_tracker_okf.paths import parse_item_path
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

BLOCKS = frozenset({"design", "plan", "execute", "finish"})
NEEDS = frozenset({"design", "plan", "execute", "finish", "resolved"})
EDGE_KEYS = frozenset({"path", "blocks", "needs"})
DEFAULT_BLOCKS = "execute"
DEFAULT_NEEDS = "resolved"
PHASE_ORDER = ("design", "plan", "execute", "finish", "done")
PHASE_RANK = {phase: rank for rank, phase in enumerate(PHASE_ORDER)}


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    path: str
    blocks: str
    needs: str


@dataclass(frozen=True, slots=True)
class DependencyIssue:
    index: int
    code: str
    detail: str
    raw: object


@dataclass(frozen=True, slots=True)
class DependencyParse:
    edges: tuple[DependencyEdge, ...]
    issues: tuple[DependencyIssue, ...]


def _entries(raw: object) -> tuple[object, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes, Mapping, DependencyEdge)):
        return (raw,)
    return tuple(raw) if isinstance(raw, Sequence) else (raw,)


def parse_dependencies(raw: object) -> DependencyParse:
    edges: list[DependencyEdge] = []
    issues: list[DependencyIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for index, entry in enumerate(_entries(raw)):
        if isinstance(entry, DependencyEdge):
            edge = entry
        elif not isinstance(entry, Mapping):
            issues.append(DependencyIssue(index, "invalid-entry", "expected a {path, blocks, needs} mapping", entry))
            continue
        else:
            unknown = sorted(str(key) for key in entry if key not in EDGE_KEYS)
            if unknown:
                issues.append(DependencyIssue(index, "unknown-keys", f"unknown keys: {unknown}", entry))
            missing = sorted(key for key in EDGE_KEYS if key not in entry)
            if missing:
                issues.append(DependencyIssue(index, "missing-keys", f"missing keys: {missing}", entry))
                continue
            path = entry.get("path")
            if not isinstance(path, str):
                issues.append(DependencyIssue(index, "empty-path", "path must be a non-empty string", entry))
                continue
            blocks = entry["blocks"]
            needs = entry["needs"]
            edge = DependencyEdge(
                path, blocks if isinstance(blocks, str) else "", needs if isinstance(needs, str) else ""
            )
        if not edge.path:
            issues.append(DependencyIssue(index, "empty-path", "path must be a non-empty string", entry))
        elif parse_item_path(edge.path) is None:
            issues.append(DependencyIssue(index, "invalid-path", "path must be a canonical item path", entry))
        if edge.blocks not in BLOCKS:
            issues.append(DependencyIssue(index, "invalid-blocks", f"blocks {edge.blocks!r} is invalid", entry))
        if edge.needs not in NEEDS:
            issues.append(DependencyIssue(index, "invalid-needs", f"needs {edge.needs!r} is invalid", entry))
        key = (edge.path, edge.blocks, edge.needs)
        if key in seen:
            issues.append(DependencyIssue(index, "duplicate-edge", f"duplicate edge {key!r}", entry))
        seen.add(key)
        if edge.path and parse_item_path(edge.path) is not None:
            edges.append(edge)
    return DependencyParse(tuple(edges), tuple(issues))


def serialize_dependencies(edges: Sequence[DependencyEdge]) -> tuple[dict[str, str], ...]:
    return tuple({"path": edge.path, "blocks": edge.blocks, "needs": edge.needs} for edge in edges)


@dataclass(frozen=True, slots=True)
class DependencyFact:
    path: str
    known: bool
    terminal: bool
    phase: str | None = None
    status: str | None = None


class DependencyNode(Protocol):
    @property
    def path(self) -> str: ...
    @property
    def phase(self) -> str | None: ...
    @property
    def work_status(self) -> str: ...


def satisfied(edge: DependencyEdge, fact: DependencyFact) -> bool:
    if not fact.known:
        return False
    if fact.terminal:
        return True
    if edge.needs == DEFAULT_NEEDS or fact.phase is None:
        return False
    return edge.needs in PHASE_RANK and fact.phase in PHASE_RANK and PHASE_RANK[fact.phase] > PHASE_RANK[edge.needs]


def gates(edge: DependencyEdge, phase: str) -> bool:
    return edge.blocks not in PHASE_RANK or phase not in PHASE_RANK or PHASE_RANK[phase] >= PHASE_RANK[edge.blocks]


def entry_phase(type: str, effort: str | None) -> str | None:
    if type != "TestGap":
        return "design"
    return None if effort is None else ("execute" if effort in {"xtra-small", "small"} else "plan")


def validate_dependencies(
    edges: Sequence[DependencyEdge], *, parent_path: str | None, self_path: str
) -> tuple[DependencyIssue, ...]:
    parsed = parse_dependencies(tuple(edges))
    issues = list(parsed.issues)
    location = parse_item_path(self_path)
    parents = set(location.ancestor_paths if location is not None else ())
    if parent_path is not None:
        parents.add(parent_path)
    for index, edge in enumerate(edges):
        if edge.path in parents:
            issues.append(DependencyIssue(index, "targets-parent", "dependency names a containing parent", edge))
        if edge.path == self_path:
            issues.append(DependencyIssue(index, "targets-self", "dependency names the new item", edge))
    return tuple(issues)


def resolve_facts(nodes: Sequence[DependencyNode], edges: Sequence[DependencyEdge]) -> tuple[DependencyFact, ...]:
    by_path = {node.path: node for node in nodes}
    facts: list[DependencyFact] = []
    for path in dict.fromkeys(edge.path for edge in edges):
        node = by_path.get(path)
        if node is None:
            facts.append(DependencyFact(path, False, False))
        else:
            facts.append(
                DependencyFact(path, True, node.work_status in TERMINAL_STATUSES, node.phase, node.work_status)
            )
    return tuple(facts)


def unmet(
    edges: Sequence[DependencyEdge], facts: Sequence[DependencyFact], phase: str
) -> tuple[tuple[DependencyEdge, DependencyFact], ...]:
    by_path = {fact.path: fact for fact in facts}
    return tuple(
        (edge, fact)
        for edge in edges
        if gates(edge, phase)
        and not satisfied(edge, fact := by_path.get(edge.path, DependencyFact(edge.path, False, False)))
    )


def describe(edge: DependencyEdge, fact: DependencyFact) -> str:
    required = "needs resolved" if edge.needs == "resolved" else f"needs {edge.needs} complete"
    observed = (
        "no work item matches this path"
        if not fact.known
        else (
            f"status {fact.status or 'unknown'}, at {fact.phase}"
            if fact.phase
            else f"status {fact.status or 'unknown'}, not started"
        )
    )
    return f"{edge.path} ({required}; {observed})"


__all__ = [
    "BLOCKS",
    "DEFAULT_BLOCKS",
    "DEFAULT_NEEDS",
    "EDGE_KEYS",
    "NEEDS",
    "PHASE_ORDER",
    "DependencyEdge",
    "DependencyFact",
    "DependencyIssue",
    "DependencyParse",
    "describe",
    "entry_phase",
    "gates",
    "parse_dependencies",
    "resolve_facts",
    "satisfied",
    "serialize_dependencies",
    "unmet",
    "validate_dependencies",
]

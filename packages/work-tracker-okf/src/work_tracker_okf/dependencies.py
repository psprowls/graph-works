"""Typed dependency edges and their phase-gating predicates."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from work_tracker_okf._selection import active_preferred_slug_index
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

BLOCKS = frozenset({"design", "plan", "execute", "finish"})
NEEDS = frozenset({"design", "plan", "execute", "resolved"})
DEFAULT_BLOCKS = "execute"
DEFAULT_NEEDS = "resolved"
EDGE_KEYS = frozenset({"slug", "blocks", "needs"})
PHASE_ORDER = ("design", "plan", "execute", "finish", "done")
PHASE_RANK = {phase: rank for rank, phase in enumerate(PHASE_ORDER)}


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    slug: str
    blocks: str = DEFAULT_BLOCKS
    needs: str = DEFAULT_NEEDS


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


def serialize_dependencies(edges: Sequence[DependencyEdge]) -> tuple[str | dict[str, str], ...]:
    return tuple(
        edge.slug
        if edge.blocks == DEFAULT_BLOCKS and edge.needs == DEFAULT_NEEDS
        else {"slug": edge.slug, "blocks": edge.blocks, "needs": edge.needs}
        for edge in edges
    )


def _entries(raw: object) -> tuple[object, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes, Mapping, DependencyEdge)):
        return (raw,)
    if isinstance(raw, Sequence):
        return tuple(raw)
    return (raw,)


def parse_dependencies(raw: object) -> DependencyParse:
    edges: list[DependencyEdge] = []
    issues: list[DependencyIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for index, entry in enumerate(_entries(raw)):
        if isinstance(entry, DependencyEdge):
            edge = entry
        elif isinstance(entry, str):
            edge = DependencyEdge(entry)
        elif isinstance(entry, Mapping):
            unknown = sorted(str(key) for key in entry if key not in EDGE_KEYS)
            if unknown:
                issues.append(DependencyIssue(index, "unknown-keys", f"unknown keys: {unknown}", entry))
            slug_value = entry.get("slug")
            slug = slug_value if isinstance(slug_value, str) else ""
            blocks = entry.get("blocks", DEFAULT_BLOCKS)
            needs = entry.get("needs", DEFAULT_NEEDS)
            edge = DependencyEdge(slug, str(blocks), str(needs))
        else:
            issues.append(DependencyIssue(index, "invalid-entry", "expected string or mapping", entry))
            continue
        if not edge.slug:
            issues.append(DependencyIssue(index, "empty-slug", "slug must be a non-empty string", entry))
        if edge.blocks not in BLOCKS:
            issues.append(DependencyIssue(index, "invalid-blocks", f"blocks {edge.blocks!r} is invalid", entry))
        if edge.needs not in NEEDS:
            issues.append(DependencyIssue(index, "invalid-needs", f"needs {edge.needs!r} is invalid", entry))
        key = (edge.slug, edge.blocks, edge.needs)
        if key in seen:
            issues.append(DependencyIssue(index, "duplicate-edge", f"duplicate edge {key!r}", entry))
        seen.add(key)
        edges.append(edge)
    return DependencyParse(tuple(edges), tuple(issues))


@dataclass(frozen=True, slots=True)
class DependencyFact:
    slug: str
    known: bool
    terminal: bool
    phase: str | None = None
    status: str | None = None


class DependencyNode(Protocol):
    @property
    def slug(self) -> str: ...

    @property
    def phase(self) -> str | None: ...

    @property
    def workflow_status(self) -> str: ...

    @property
    def archived(self) -> bool: ...


def satisfied(edge: DependencyEdge, fact: DependencyFact) -> bool:
    if not fact.known:
        return False
    if fact.terminal:
        return True
    if edge.needs == DEFAULT_NEEDS or fact.phase is None:
        return False
    if edge.needs not in PHASE_RANK or fact.phase not in PHASE_RANK:
        return False
    return PHASE_RANK[fact.phase] > PHASE_RANK[edge.needs]


def gates(edge: DependencyEdge, phase: str) -> bool:
    if edge.blocks not in PHASE_RANK or phase not in PHASE_RANK:
        return True
    return PHASE_RANK[phase] >= PHASE_RANK[edge.blocks]


def entry_phase(type: str, effort: str | None) -> str | None:
    if type != "TestGap":
        return "design"
    if effort is None:
        return None
    return "execute" if effort in {"xtra-small", "small"} else "plan"


def validate_dependencies(
    edges: Sequence[DependencyEdge],
    *,
    parent: str | None,
    self_slug: str,
) -> tuple[DependencyIssue, ...]:
    parsed = parse_dependencies(tuple(edges))
    issues = list(parsed.issues)
    for index, edge in enumerate(edges):
        if edge.slug == parent:
            issues.append(DependencyIssue(index, "targets-parent", "dependency names parent", edge))
        if edge.slug == self_slug:
            issues.append(DependencyIssue(index, "targets-self", "dependency names the new item", edge))
    return tuple(issues)


def resolve_facts(
    nodes: Sequence[DependencyNode],
    edges: Sequence[DependencyEdge],
) -> tuple[DependencyFact, ...]:
    by_slug = active_preferred_slug_index(nodes)
    facts: list[DependencyFact] = []
    for slug in dict.fromkeys(edge.slug for edge in edges):
        node = by_slug.get(slug)
        if node is None:
            facts.append(DependencyFact(slug, known=False, terminal=False))
        else:
            facts.append(
                DependencyFact(
                    slug,
                    known=True,
                    terminal=node.workflow_status in TERMINAL_STATUSES,
                    phase=node.phase,
                    status=node.workflow_status,
                )
            )
    return tuple(facts)


def unmet(
    edges: Sequence[DependencyEdge],
    facts: Sequence[DependencyFact],
    phase: str,
) -> tuple[tuple[DependencyEdge, DependencyFact], ...]:
    by_slug = {fact.slug: fact for fact in facts}
    return tuple(
        (edge, fact)
        for edge in edges
        if gates(edge, phase)
        and not satisfied(
            edge,
            fact := by_slug.get(edge.slug, DependencyFact(edge.slug, known=False, terminal=False)),
        )
    )


def describe(edge: DependencyEdge, fact: DependencyFact) -> str:
    required = "needs resolved" if edge.needs == "resolved" else f"needs {edge.needs} complete"
    if not fact.known:
        observed = "no work item matches this slug"
    elif edge.needs == "resolved":
        observed = f"status {fact.status or 'unknown'}, " + (f"at {fact.phase}" if fact.phase else "not started")
    else:
        observed = f"it is at {fact.phase}" if fact.phase else "it is not started"
    if edge.blocks not in BLOCKS:
        observed += f"; blocks {edge.blocks!r} is invalid, so every phase is gated"
    return f"{edge.slug} ({required}; {observed})"

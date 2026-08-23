from dataclasses import dataclass

from work_tracker_okf.dependencies import (
    DependencyEdge,
    DependencyFact,
    entry_phase,
    gates,
    parse_dependencies,
    resolve_facts,
    satisfied,
    serialize_dependencies,
    validate_dependencies,
)


@dataclass(frozen=True)
class Node:
    path: str
    phase: str | None
    work_status: str
    archived: bool = False


def test_only_path_keyed_mappings_are_accepted() -> None:
    parsed = parse_dependencies(
        [
            {"path": "work/release/children/epic", "blocks": "plan", "needs": "design"},
            "work/legacy-string",
            {"slug": "legacy-slug"},
        ]
    )
    assert parsed.edges == (DependencyEdge("work/release/children/epic", "plan", "design"),)
    assert [issue.code for issue in parsed.issues] == ["invalid-entry", "unknown-keys", "missing-keys"]


def test_dependencies_require_path_blocks_and_needs() -> None:
    parsed = parse_dependencies(
        [
            {"path": "work/a", "needs": "resolved"},
            {"path": "work/a", "blocks": "execute"},
        ]
    )
    assert parsed.edges == ()
    assert [issue.code for issue in parsed.issues] == ["missing-keys", "missing-keys"]


def test_dependencies_reject_noncanonical_paths() -> None:
    parsed = parse_dependencies(
        [
            {"path": "a-slug", "blocks": "execute", "needs": "resolved"},
            {"path": "/work/a", "blocks": "execute", "needs": "resolved"},
            {"path": "work/a.md", "blocks": "execute", "needs": "resolved"},
        ]
    )
    assert parsed.edges == ()
    assert [issue.code for issue in parsed.issues] == ["invalid-path", "invalid-path", "invalid-path"]


def test_dependencies_always_serialize_as_complete_mappings() -> None:
    assert serialize_dependencies((DependencyEdge("work/a", "execute", "resolved"),)) == (
        {"path": "work/a", "blocks": "execute", "needs": "resolved"},
    )


def test_distinct_gates_to_one_full_path_are_legal() -> None:
    edges = (DependencyEdge("work/a", "plan", "design"), DependencyEdge("work/a", "execute", "resolved"))
    assert validate_dependencies(edges, parent_path=None, self_path="work/new") == ()


def test_dependencies_reject_self_and_containing_parent_targets() -> None:
    edges = (
        DependencyEdge("work/release", "execute", "resolved"),
        DependencyEdge("work/release/children/epic", "execute", "resolved"),
    )
    issues = validate_dependencies(edges, parent_path="work/release", self_path="work/release/children/epic")
    assert {issue.code for issue in issues} == {"targets-parent", "targets-self"}


def test_dependency_facts_resolve_exact_full_paths() -> None:
    facts = resolve_facts(
        (Node("work/a", "execute", "in-progress"),),
        (DependencyEdge("work/a", "execute", "resolved"), DependencyEdge("work/missing", "execute", "resolved")),
    )
    assert facts == (
        DependencyFact("work/a", known=True, terminal=False, phase="execute", status="in-progress"),
        DependencyFact("work/missing", known=False, terminal=False),
    )


def test_dependency_parser_and_gate_predicates_cover_invalid_values_and_boundaries() -> None:
    duplicate = DependencyEdge("work/a", "execute", "resolved")
    parsed = parse_dependencies(
        [
            {"path": 7, "blocks": "execute", "needs": "resolved"},
            DependencyEdge("", "bad", "bad"),
            duplicate,
            duplicate,
        ]
    )
    assert {issue.code for issue in parsed.issues} >= {
        "empty-path",
        "invalid-blocks",
        "invalid-needs",
        "duplicate-edge",
    }
    assert satisfied(duplicate, DependencyFact("work/a", known=True, terminal=True))
    assert not satisfied(duplicate, DependencyFact("work/a", known=False, terminal=False))
    assert not satisfied(duplicate, DependencyFact("work/a", known=True, terminal=False, phase=None))
    assert satisfied(
        DependencyEdge("work/a", "execute", "design"),
        DependencyFact("work/a", known=True, terminal=False, phase="plan"),
    )
    assert gates(duplicate, "finish") and gates(DependencyEdge("work/a", "unknown", "resolved"), "design")
    assert entry_phase("Feature", None) == "design"
    assert entry_phase("TestGap", None) is None
    assert entry_phase("TestGap", "small") == "execute"
    assert entry_phase("TestGap", "large") == "plan"

from dataclasses import dataclass

import pytest
from work_tracker_okf.dependencies import (
    DependencyEdge,
    DependencyFact,
    describe,
    entry_phase,
    gates,
    parse_dependencies,
    resolve_facts,
    satisfied,
    serialize_dependencies,
    unmet,
    validate_dependencies,
)


@dataclass(frozen=True)
class Node:
    slug: str
    phase: str | None
    workflow_status: str
    archived: bool = False


def test_string_mapping_single_mapping_and_edge_normalize_in_order() -> None:
    parsed = parse_dependencies(
        [
            "a",
            {"slug": "b", "blocks": "plan", "needs": "design"},
            DependencyEdge("c", blocks="finish", needs="execute"),
        ]
    )
    assert parsed.edges == (
        DependencyEdge("a"),
        DependencyEdge("b", blocks="plan", needs="design"),
        DependencyEdge("c", blocks="finish", needs="execute"),
    )
    assert parsed.issues == ()


def test_defaults_serialize_as_strings_and_non_defaults_as_mappings() -> None:
    assert serialize_dependencies((DependencyEdge("a"), DependencyEdge("b", blocks="plan", needs="design"))) == (
        "a",
        {"slug": "b", "blocks": "plan", "needs": "design"},
    )


def test_tolerant_parse_preserves_invalid_values_as_issues() -> None:
    parsed = parse_dependencies([{"slug": "a", "blocks": "build", "needs": "started", "extra": True}, 42])
    assert parsed.edges[0] == DependencyEdge("a", blocks="build", needs="started")
    assert {issue.code for issue in parsed.issues} == {
        "unknown-keys",
        "invalid-blocks",
        "invalid-needs",
        "invalid-entry",
    }
    assert parsed.issues[-1].raw == 42


def test_one_mapping_is_one_entry_not_an_iterable_of_keys() -> None:
    parsed = parse_dependencies({"slug": "a", "blocks": "plan", "needs": "design"})
    assert parsed.edges == (DependencyEdge("a", blocks="plan", needs="design"),)


@pytest.mark.parametrize(
    ("edges", "parent", "self_slug", "code"),
    [
        ((DependencyEdge(""),), None, "new", "empty-slug"),
        ((DependencyEdge("parent"),), "parent", "new", "targets-parent"),
        ((DependencyEdge("new"),), None, "new", "targets-self"),
        ((DependencyEdge("a"), DependencyEdge("a")), None, "new", "duplicate-edge"),
        ((DependencyEdge("a", blocks="build"),), None, "new", "invalid-blocks"),
        ((DependencyEdge("a", needs="started"),), None, "new", "invalid-needs"),
    ],
)
def test_strict_write_checks(edges, parent, self_slug, code) -> None:
    assert code in {issue.code for issue in validate_dependencies(edges, parent=parent, self_slug=self_slug)}


def test_distinct_gates_for_one_slug_are_legal() -> None:
    issues = validate_dependencies(
        (DependencyEdge("a", blocks="plan", needs="design"), DependencyEdge("a")),
        parent=None,
        self_slug="new",
    )
    assert issues == ()


def test_needs_is_complete_not_entered_and_terminal_satisfies_everything() -> None:
    edge = DependencyEdge("a", blocks="plan", needs="plan")
    assert (
        satisfied(
            edge,
            DependencyFact("a", known=True, terminal=False, phase="plan", status="accepted"),
        )
        is False
    )
    assert (
        satisfied(
            edge,
            DependencyFact("a", known=True, terminal=False, phase="execute", status="in-progress"),
        )
        is True
    )
    assert (
        satisfied(
            edge,
            DependencyFact("a", known=True, terminal=True, phase=None, status="resolved"),
        )
        is True
    )


def test_blocks_applies_from_the_named_phase_onward() -> None:
    edge = DependencyEdge("a", blocks="execute")
    assert [phase for phase in ("design", "plan", "execute", "finish") if gates(edge, phase)] == ["execute", "finish"]


def test_unknown_unentered_and_invalid_facts_fail_closed() -> None:
    assert satisfied(DependencyEdge("a"), DependencyFact("a", known=False, terminal=False)) is False
    assert (
        satisfied(
            DependencyEdge("a", needs="design"),
            DependencyFact("a", known=True, terminal=False, phase=None, status="open"),
        )
        is False
    )
    assert gates(DependencyEdge("a", blocks="build"), "design") is True


def test_invalid_evaluated_phase_is_gated_fail_closed() -> None:
    assert gates(DependencyEdge("a", blocks="plan"), "build") is True


@pytest.mark.parametrize(
    ("type_", "effort", "expected"),
    [
        ("Feature", None, "design"),
        ("TestGap", None, None),
        ("TestGap", "small", "execute"),
        ("TestGap", "medium", "plan"),
    ],
)
def test_entry_phase_keeps_test_gaps_out_of_design(
    type_: str,
    effort: str | None,
    expected: str | None,
) -> None:
    assert entry_phase(type_, effort) == expected


def test_resolve_facts_deduplicates_edges_and_preserves_terminal_state() -> None:
    facts = resolve_facts(
        (
            Node("active", "execute", "in-progress"),
            Node("done", None, "resolved"),
        ),
        (
            DependencyEdge("active", blocks="plan", needs="design"),
            DependencyEdge("missing"),
            DependencyEdge("active"),
            DependencyEdge("done"),
        ),
    )

    assert facts == (
        DependencyFact("active", known=True, terminal=False, phase="execute", status="in-progress"),
        DependencyFact("missing", known=False, terminal=False),
        DependencyFact("done", known=True, terminal=True, phase=None, status="resolved"),
    )


def test_resolve_facts_prefers_active_metadata_over_an_archived_twin() -> None:
    edge = DependencyEdge("dep", blocks="execute", needs="resolved")

    facts = resolve_facts(
        (
            Node("dep", "execute", "in-progress"),
            Node("dep", None, "resolved", archived=True),
        ),
        (edge,),
    )

    assert facts == (DependencyFact("dep", known=True, terminal=False, phase="execute", status="in-progress"),)


def test_unmet_filters_by_current_gate_and_dependency_fact() -> None:
    early = DependencyEdge("missing", blocks="execute")
    blocked = DependencyEdge("active", blocks="plan", needs="execute")
    satisfied_edge = DependencyEdge("done", blocks="plan", needs="resolved")
    facts = (
        DependencyFact("active", known=True, terminal=False, phase="execute", status="in-progress"),
        DependencyFact("done", known=True, terminal=True, phase=None, status="resolved"),
    )

    assert unmet((early, blocked, satisfied_edge), facts, "plan") == ((blocked, facts[0]),)


@pytest.mark.parametrize(
    ("edge", "fact", "expected"),
    [
        (
            DependencyEdge("missing"),
            DependencyFact("missing", known=False, terminal=False),
            "missing (needs resolved; no work item matches this slug)",
        ),
        (
            DependencyEdge("active", blocks="plan", needs="design"),
            DependencyFact("active", known=True, terminal=False, phase="plan", status="accepted"),
            "active (needs design complete; it is at plan)",
        ),
        (
            DependencyEdge("invalid", blocks="build"),
            DependencyFact("invalid", known=True, terminal=False, phase=None, status="open"),
            "invalid (needs resolved; status open, not started; blocks 'build' is invalid, so every phase is gated)",
        ),
    ],
)
def test_describe_names_the_required_and_observed_dependency_state(
    edge: DependencyEdge,
    fact: DependencyFact,
    expected: str,
) -> None:
    assert describe(edge, fact) == expected

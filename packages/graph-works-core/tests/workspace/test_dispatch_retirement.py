import pytest
from graph_works_core.workspace.dispatch import (
    dispatch_attributes,
    packaged_rules,
    packaged_rules_matching,
    parse_rules,
    resolve_dispatch,
)
from graph_works_core.workspace.errors import WorkspaceError
from work_tracker_okf.pipeline import ATTRIBUTES, VARIANT_REPLACEMENTS
from work_tracker_okf.workflow import Dispatch, RouteState

_ALL = frozenset(ATTRIBUTES)


@pytest.mark.parametrize("variant", sorted(VARIANT_REPLACEMENTS))
def test_each_variant_is_refused_with_its_replacement(variant: str) -> None:
    with pytest.raises(WorkspaceError) as caught:
        parse_rules([{"match": {"variant": variant}, "agent": "codex"}], source="dispatch.yaml", attributes=_ALL)
    assert (
        "dispatch.yaml: rule 0: match.variant: routing variants are retired; replace with "
        f"{variant} -> {VARIANT_REPLACEMENTS[variant]}" in str(caught.value)
    )


def test_variant_list_names_every_replacement() -> None:
    with pytest.raises(WorkspaceError) as caught:
        parse_rules(
            [{"match": {"variant": ["diagnosis", "reconcile", "planned", "unplanned"]}, "agent": "codex"}],
            source="dispatch.local.yaml",
            attributes=_ALL,
        )
    for value in ("diagnosis", "reconcile", "planned", "unplanned"):
        assert f"{value} -> {VARIANT_REPLACEMENTS[value]}" in str(caught.value)


def test_packaged_rules_are_the_ten_named_rules_in_order() -> None:
    rules = packaged_rules()
    assert [r.origin.name for r in rules] == [
        "design",
        "design-bug",
        "design-parent",
        "design-reconcile",
        "plan",
        "plan-parent",
        "plan-reconcile",
        "execute",
        "execute-planned",
        "finish",
    ]
    assert [r.origin.index for r in rules] == list(range(10))
    assert all(r.origin.source == "packaged" for r in rules)
    assert dict(rules[6].match) == {"stage": "plan", "spec_stale": True}


def test_later_packaged_rule_wins_with_provenance() -> None:
    attributes = {"stage": "design", "type": "Bug", "has_spec": True, "has_plan": False, "spec_stale": False}
    assert [r.origin.name for r in packaged_rules_matching(attributes)] == ["design", "design-bug", "design-reconcile"]
    resolution = resolve_dispatch(attributes, rules=())
    assert resolution.profile.skill == "gw:reconciling-spec"
    assert resolution.provenance["skill"].rule.name == "design-reconcile"


def test_attributes_matching_no_packaged_rule_are_refused() -> None:
    with pytest.raises(WorkspaceError, match="no packaged dispatch rule matches"):
        resolve_dispatch({"type": "Bug"}, rules=())


def test_dispatch_attributes_has_spec_stale_and_no_variant() -> None:
    state = RouteState(type="Feature", work_status="open", phase="plan", stale_spec=("work/x",))
    attributes = dispatch_attributes(state, Dispatch("plan"))
    assert "variant" not in attributes
    assert attributes["spec_stale"] is True


def test_stale_testgap_entry_starts_a_plan_instead_of_reconciling() -> None:
    state = RouteState(type="TestGap", work_status="open", phase=None, effort="large", stale_spec=("work/x",))
    attributes = dispatch_attributes(state, Dispatch("plan"))
    assert attributes["spec_stale"] is False
    resolution = resolve_dispatch(attributes, rules=())
    assert resolution.profile.skill == "superpowers:writing-plans"

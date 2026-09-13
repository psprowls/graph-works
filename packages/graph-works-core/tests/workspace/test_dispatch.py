from __future__ import annotations

from types import MappingProxyType

import pytest
from graph_works_core.workspace.dispatch import parse_rules, resolve_dispatch
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.pipeline import ATTEND_TAIL, EXECUTE_TAIL

ATTRIBUTES = frozenset({"stage", "type", "effort", "blast_radius", "has_spec", "has_plan", "variant"})


def _resolve(*raw: object, attributes: dict[str, str | bool | None] | None = None):
    rules = parse_rules(list(raw), source="/workspace/dispatch.yaml", attributes=ATTRIBUTES)
    return resolve_dispatch(attributes or {"stage": "execute", "variant": "planned"}, rules=rules)


def test_agent_switch_clears_inherited_preferences():
    rules = parse_rules(
        [
            {"match": {}, "agent": "claude", "model": "opus", "reasoning_effort": "high"},
            {"match": {"stage": "execute"}, "agent": "codex"},
        ],
        source="/workspace/dispatch.yaml",
        attributes=ATTRIBUTES,
    )
    result = resolve_dispatch({"stage": "execute", "variant": "planned"}, rules=rules)
    assert result.profile.agent == "codex"
    assert result.profile.model is None
    assert result.profile.reasoning_effort is None
    assert result.provenance["model"].reason == "agent-change"
    assert result.provenance["model"].rule.index == 1


@pytest.mark.parametrize(
    ("rules", "agent", "model", "effort"),
    [
        (
            [
                {"match": {}, "agent": "claude", "model": "opus", "reasoning_effort": "high"},
                {"match": {"stage": "execute"}, "agent": "claude"},
            ],
            "claude",
            "opus",
            "high",
        ),
        (
            [
                {"match": {}, "agent": "claude", "model": "opus", "reasoning_effort": "high"},
                {"match": {}, "agent": "codex"},
                {"match": {}, "agent": "claude"},
            ],
            "claude",
            None,
            None,
        ),
        (
            [{"match": {}, "agent": "claude", "model": "opus"}, {"match": {}, "model": "gpt-5", "agent": "codex"}],
            "codex",
            "gpt-5",
            None,
        ),
        (
            [{"match": {}, "agent": "claude", "model": "opus"}, {"match": {}, "agent": "codex", "model": "gpt-5"}],
            "codex",
            "gpt-5",
            None,
        ),
        ([{"match": {}, "reasoning_effort": "high"}, {"match": {}, "model": "opus"}], "claude", "opus", "high"),
    ],
)
def test_agent_and_preference_cascade(rules, agent, model, effort):
    result = _resolve(*rules)
    assert (result.profile.agent, result.profile.model, result.profile.reasoning_effort) == (agent, model, effort)


def test_clearing_model_alone_retains_effort_and_is_finally_refused():
    with pytest.raises(WorkspaceError, match="Set a model or clear reasoning_effort\\."):
        _resolve(
            {"match": {}, "model": "opus", "reasoning_effort": "high"},
            {"match": {}, "model": None},
        )


@pytest.mark.parametrize(
    ("stage", "variant", "skill", "mode", "tail"),
    [
        ("design", "exploration", "superpowers:brainstorming", "attend", ATTEND_TAIL),
        ("design", "diagnosis", "superpowers:systematic-debugging", "attend", ATTEND_TAIL),
        ("design", "reconcile", "gw:reconciling-spec", "autonomous", None),
        ("design", "epic-design", "gw:epic-design", "attend", ATTEND_TAIL),
        ("plan", "decompose", "gw:planning-epics", "autonomous", None),
        ("plan", "single", "superpowers:writing-plans", "autonomous", None),
        ("execute", "planned", "superpowers:subagent-driven-development", "autonomous", EXECUTE_TAIL),
        ("execute", "unplanned", "superpowers:test-driven-development", "autonomous", EXECUTE_TAIL),
        ("finish", "branch", "superpowers:finishing-a-development-branch", "relay", "ours"),
    ],
)
def test_packaged_profile_matrix(stage, variant, skill, mode, tail):
    raw = () if variant != "branch" else ({"match": {"variant": "branch"}, "prompt_tail": "ours"},)
    result = _resolve(*raw, attributes={"stage": stage, "variant": variant})
    assert (result.profile.skill, result.profile.mode, result.profile.prompt_tail) == (skill, mode, tail)
    assert result.profile.agent == "claude"
    assert result.profile.model is None
    assert result.profile.reasoning_effort is None
    assert set(result.provenance) == {"skill", "mode", "prompt_tail", "agent", "model", "reasoning_effort"}


def test_packaged_only_finish_is_refused():
    with pytest.raises(WorkspaceError, match=r"relay.*prompt_tail"):
        resolve_dispatch({"stage": "finish", "variant": "branch"}, rules=())


def test_constraints_are_anded_and_lists_match_any_member():
    result = _resolve(
        {"match": {"stage": ["design", "execute"], "has_spec": True}, "model": "winner"},
        attributes={"stage": "execute", "variant": "planned", "has_spec": True},
    )
    assert result.profile.model == "winner"


@pytest.mark.parametrize("value", [None, False, 1])
def test_missing_null_and_non_boolean_values_do_not_match_boolean_constraints(value):
    attributes = {"stage": "execute", "variant": "planned"}
    if value is not None:
        attributes["has_spec"] = value  # type: ignore[assignment]
    result = _resolve({"match": {"has_spec": True}, "model": "wrong"}, attributes=attributes)
    assert result.profile.model is None


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"agent": "codex"}, "match"),
        ({"match": [], "agent": "codex"}, "match"),
        ({"match": {}, "unknown": "x"}, "unknown"),
        ({"match": {}, "name": "  ", "agent": "codex"}, "name"),
        ({"match": {}}, "profile"),
        ({"match": {"invented": "x"}, "agent": "codex"}, "invented"),
        ({"match": {"stage": None}, "agent": "codex"}, "null"),
        ({"match": {"stage": []}, "agent": "codex"}, "non-empty"),
        ({"match": {"stage": ["execute", True]}, "agent": "codex"}, "homogeneous"),
        ({"match": {"stage": "bogus"}, "agent": "codex"}, "stage"),
        ({"match": {"has_spec": "true"}, "agent": "codex"}, "boolean"),
        ({"match": {}, "agent": None}, "agent"),
        ({"match": {}, "skill": ""}, "skill"),
        ({"match": {}, "mode": "bogus"}, "mode"),
        ({"match": {}, "model": "  "}, "model"),
        ({"match": {}, "reasoning_effort": 3}, "reasoning_effort"),
    ],
)
def test_invalid_rule_schema_is_refused_even_when_rule_would_not_match(raw, message):
    with pytest.raises(WorkspaceError, match=message):
        parse_rules([raw], source="/workspace/dispatch.yaml", attributes=ATTRIBUTES)


def test_parse_rules_defensively_freezes_caller_mappings_and_preserves_names():
    match = {"stage": "execute"}
    raw = [{"name": "same", "match": match, "model": "one"}, {"name": "same", "match": {}, "model": "two"}]
    rules = parse_rules(raw, source="/workspace/dispatch.yaml", attributes=ATTRIBUTES)
    match["stage"] = "design"
    raw[0]["model"] = "changed"
    assert rules[0].match == MappingProxyType({"stage": "execute"})
    assert rules[0].fields == MappingProxyType({"model": "one"})
    assert [rule.origin.name for rule in rules] == ["same", "same"]
    with pytest.raises(TypeError):
        rules[0].fields["model"] = "mutated"  # type: ignore[index]


def test_explicit_null_rule_name_reports_file_rule_and_field():
    with pytest.raises(WorkspaceError) as error:
        parse_rules(
            [{"name": None, "match": {}, "agent": "codex"}],
            source="/workspace/dispatch.yaml",
            attributes=ATTRIBUTES,
        )
    assert "/workspace/dispatch.yaml" in str(error.value)
    assert "rule 0" in str(error.value)
    assert "name" in str(error.value)

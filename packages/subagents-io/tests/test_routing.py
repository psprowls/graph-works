"""Model routing: resolution order, and what the validator will and will not say.

The vocabularies below are lowercase because the *caller* supplies them. Band 1
has no opinion about what a `kind` looks like — supplying `{Epic, Feature, …}`
instead is a caller's decision and changes nothing here.
"""

from __future__ import annotations

from subagents_io.routing import ModelResolution, resolve_model, validate_rules

BLOCK = {
    "max_parallel": 2,
    "models": {"design": "claude-fable-5", "execute": "claude-sonnet-5"},
    "overrides": [
        {
            "match": {"phase": "execute", "kind": ["bug", "tech-debt"], "effort": ["xtra-small", "small"]},
            "model": "claude-haiku-4-5",
        },
        {"match": {"phase": "plan", "kind": "epic"}, "model": "claude-fable-5", "reasoning_effort": "high"},
        {"match": {"phase": "plan"}, "model": "claude-sonnet-5"},
    ],
}

VOCABULARIES = {
    "phase": frozenset({"design", "plan", "execute", "finish"}),
    "kind": frozenset({"epic", "feature", "bug", "tech-debt", "test-gap", "spike"}),
    "effort": frozenset({"xtra-small", "small", "medium", "large", "xtra-large"}),
}


def attrs(phase, kind, effort=None):
    return {"phase": phase, "kind": kind, "effort": effort}


# --- resolution ------------------------------------------------------------


def test_override_first_match_wins():
    # Rules 2 and 3 both match (plan, epic); rule 2 is first and wins outright.
    got = resolve_model(BLOCK, attrs("plan", "epic", "large"), default_key="phase")
    assert got == ModelResolution("claude-fable-5", "high")


def test_later_rule_matches_when_earlier_rules_miss():
    got = resolve_model(BLOCK, attrs("plan", "feature"), default_key="phase")
    assert got == ModelResolution("claude-sonnet-5", None)


def test_list_membership_and_scalar_equality():
    got = resolve_model(BLOCK, attrs("execute", "bug", "small"), default_key="phase")
    assert got == ModelResolution("claude-haiku-4-5", None)


def test_constraint_on_a_none_attribute_never_matches():
    # Rule 1 constrains effort; an item with effort None falls through to the
    # models tier instead of matching it.
    got = resolve_model(BLOCK, attrs("execute", "bug", None), default_key="phase")
    assert got == ModelResolution("claude-sonnet-5", None)


def test_constraint_on_an_omitted_attribute_never_matches():
    # Same rule, but the caller does not supply the dimension at all.
    got = resolve_model(BLOCK, {"phase": "execute", "kind": "bug"}, default_key="phase")
    assert got == ModelResolution("claude-sonnet-5", None)


def test_absent_match_key_is_wildcard():
    block = {"overrides": [{"match": {"phase": "design"}, "model": "m"}]}
    assert resolve_model(block, attrs("design", "spike"), default_key="phase") == ModelResolution("m", None)


def test_unknown_match_key_does_not_match_and_does_not_raise():
    # `item[key]` used to KeyError here; `attrs.get(key)` fails closed instead.
    block = {"overrides": [{"match": {"owner": "pat"}, "model": "m"}]}
    assert resolve_model(block, attrs("plan", "feature"), default_key="phase") is None


def test_models_tier_fallback():
    got = resolve_model(BLOCK, attrs("design", "feature", "medium"), default_key="phase")
    assert got == ModelResolution("claude-fable-5", None)


def test_default_key_none_skips_the_models_tier():
    # `models` is populated and would match on "design"; without a default_key
    # the tier is not consulted at all.
    assert resolve_model(BLOCK, attrs("design", "feature", "medium")) is None


def test_no_match_and_no_models_entry_returns_none():
    assert resolve_model(BLOCK, attrs("finish", "feature", "medium"), default_key="phase") is None


def test_empty_block_returns_none():
    assert resolve_model({}, attrs("design", "feature"), default_key="phase") is None


# --- validation ------------------------------------------------------------


def test_validate_clean_block():
    assert validate_rules(BLOCK, VOCABULARIES, default_key="phase") == []


def test_validate_empty_block():
    assert validate_rules({}, VOCABULARIES, default_key="phase") == []


def test_validate_rejects_values_outside_their_vocabulary():
    block = {"overrides": [{"match": {"phase": "done", "kind": "bugg", "effort": "xs"}, "model": "m"}]}
    errors = validate_rules(block, VOCABULARIES, default_key="phase")
    assert len(errors) == 3
    assert any("phase" in e and "'done'" in e for e in errors)
    assert any("kind" in e and "'bugg'" in e for e in errors)
    assert any("effort" in e and "'xs'" in e for e in errors)


def test_validate_checks_values_inside_lists():
    block = {"overrides": [{"match": {"effort": ["small", "s"]}, "model": "m"}]}
    errors = validate_rules(block, VOCABULARIES, default_key="phase")
    assert len(errors) == 1
    assert "'s'" in errors[0]


def test_validate_reports_an_unknown_match_key():
    # The rule is dead — _matches can never satisfy a key the caller did not
    # declare — so silence would be the wrong answer.
    block = {"overrides": [{"match": {"owner": "pat"}, "model": "m"}]}
    errors = validate_rules(block, VOCABULARIES, default_key="phase")
    assert errors == ["overrides[0].match: unknown key 'owner'"]


def test_validate_reports_a_models_key_outside_its_vocabulary():
    block = {"models": {"design": "m", "done": "m"}}
    errors = validate_rules(block, VOCABULARIES, default_key="phase")
    assert len(errors) == 1
    assert errors[0].startswith("models: 'done' not in ")


def test_validate_skips_the_models_tier_without_a_default_key():
    block = {"models": {"done": "m"}}
    assert validate_rules(block, VOCABULARIES) == []

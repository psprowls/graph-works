"""Model routing over a rules block: which model runs a dispatch, if any.

Pure resolution and pure validation, generic in their dimensions. The rules
name their own match keys; the caller supplies both the attribute values and
the vocabularies those keys are checked against. Nothing here spells a
work-item word, so a fourth match dimension is a caller-only change.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelResolution:
    """A resolved worker model, plus the launched agent's reasoning effort when
    an override set one. Distinct from a rule's `match.effort`, which is an
    attribute of the thing being dispatched — deliberately different names."""

    model: str
    reasoning_effort: str | None = None


def _matches(match: Mapping[str, Any], attrs: Mapping[str, str | None]) -> bool:
    for key, constraint in match.items():
        value = attrs.get(key)
        if value is None:
            # A constraint on an attribute the caller did not supply — or
            # supplied as None — never matches.
            return False
        allowed = constraint if isinstance(constraint, list) else [constraint]
        if value not in allowed:
            return False
    return True


def resolve_model(
    rules: Mapping[str, Any],
    attrs: Mapping[str, str | None],
    *,
    default_key: str | None = None,
) -> ModelResolution | None:
    """Resolve the worker model for one dispatch.

    First-match-wins over `overrides` (scalar = equality, list = membership,
    absent match key = wildcard), then `models[attrs[default_key]]` when a
    `default_key` is given, then None — meaning "inherit the session model".

    Assumes a structurally valid block; does not re-validate.
    """
    for rule in rules.get("overrides", []):
        if _matches(rule["match"], attrs):
            return ModelResolution(rule["model"], rule.get("reasoning_effort"))
    if default_key is not None:
        model = (rules.get("models") or {}).get(attrs.get(default_key))
        if model:
            return ModelResolution(model, None)
    return None


def validate_rules(
    rules: Mapping[str, Any],
    vocabularies: Mapping[str, Collection[str]],
    *,
    default_key: str | None = None,
) -> list[str]:
    """Membership-check a rules block against caller-supplied vocabularies.

    Reports override match values outside their vocabulary, match keys outside
    `vocabularies` (which can never match, so the rule is dead), and — when a
    `default_key` is given — `models` keys outside `vocabularies[default_key]`.

    Returns human-readable error strings; empty list = clean.
    """
    errors: list[str] = []
    for i, rule in enumerate(rules.get("overrides", [])):
        for key, constraint in rule.get("match", {}).items():
            valid = vocabularies.get(key)
            if valid is None:
                errors.append(f"overrides[{i}].match: unknown key {key!r}")
                continue
            for v in constraint if isinstance(constraint, list) else [constraint]:
                if v not in valid:
                    errors.append(f"overrides[{i}].match.{key}: {v!r} not in {sorted(valid)}")
    valid_default = vocabularies.get(default_key) if default_key is not None else None
    if valid_default is not None:
        for key in rules.get("models") or {}:
            if key not in valid_default:
                errors.append(f"models: {key!r} not in {sorted(valid_default)}")
    return errors

"""Pure validation and cascading resolution for workspace dispatch rules."""

from __future__ import annotations

import typing
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from subagents_io.dispatch import DISPATCH_MODES
from work_tracker_okf.vocabulary import BLAST_RADII, EFFORTS, TYPES
from work_tracker_okf.workflow import Dispatch, RouteState, Stage, Variant

from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.pipeline import PACKAGED_PIPELINE, check_skill_name

AttributeValue = str | bool | None
ProfileValue = str | None

_STAGES = frozenset(typing.get_args(Stage))
_VARIANTS = frozenset(typing.get_args(Variant))
_ATTRIBUTE_VALUES: Mapping[str, frozenset[str] | None] = MappingProxyType(
    {
        "stage": _STAGES,
        "variant": _VARIANTS,
        "type": TYPES,
        "effort": EFFORTS,
        "blast_radius": BLAST_RADII,
        "has_spec": None,
        "has_plan": None,
    }
)
_PROFILE_FIELDS = frozenset({"skill", "mode", "prompt_tail", "agent", "model", "reasoning_effort"})
_OPTIONAL_FIELDS = frozenset({"prompt_tail", "model", "reasoning_effort"})
_AGENTS = frozenset({"claude", "codex"})


@dataclass(frozen=True, slots=True)
class RuleOrigin:
    source: str
    index: int
    name: str | None


@dataclass(frozen=True, slots=True)
class FieldOrigin:
    rule: RuleOrigin
    reason: str


@dataclass(frozen=True, slots=True)
class DispatchRule:
    match: Mapping[str, str | bool | tuple[str | bool, ...]]
    fields: Mapping[str, ProfileValue]
    origin: RuleOrigin


@dataclass(frozen=True, slots=True)
class DispatchProfile:
    skill: str
    mode: str
    prompt_tail: str | None
    agent: str
    model: str | None
    reasoning_effort: str | None


@dataclass(frozen=True, slots=True)
class DispatchResolution:
    profile: DispatchProfile
    provenance: Mapping[str, FieldOrigin]


class DispatchProfileError(WorkspaceError):
    """An invalid final profile, with the planner's stable blocker category."""

    def __init__(self, message: str, *, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


def dispatch_attributes(state: RouteState, dispatch: Dispatch) -> Mapping[str, AttributeValue]:
    """Project authoritative lifecycle and artifact facts for both consumers."""
    return MappingProxyType(
        {
            "stage": dispatch.stage,
            "variant": dispatch.variant,
            "type": state.type,
            "effort": state.effort,
            "blast_radius": state.blast_radius,
            "has_spec": state.has_spec_doc,
            "has_plan": state.has_plan_doc,
        }
    )


def _refuse(source: str, index: int | None, message: str) -> typing.NoReturn:
    location = source if index is None else f"{source}: rule {index}"
    raise WorkspaceError(f"{location}: {message}")


def _constraint(value: object, *, attribute: str, source: str, index: int) -> str | bool | tuple[str | bool, ...]:
    if value is None:
        _refuse(source, index, f"match.{attribute}: null constraints are not allowed")
    values = value if isinstance(value, list) else [value]
    if not values:
        _refuse(source, index, f"match.{attribute}: list must be non-empty")
    if not all(type(item) is type(values[0]) for item in values):
        _refuse(source, index, f"match.{attribute}: list values must be homogeneous")
    allowed = _ATTRIBUTE_VALUES[attribute]
    if allowed is None:
        if not all(type(item) is bool for item in values):
            _refuse(source, index, f"match.{attribute}: expects a boolean or homogeneous boolean list")
    elif not all(isinstance(item, str) and item in allowed for item in values):
        _refuse(source, index, f"match.{attribute}: value must be one of {sorted(allowed)}")
    copied = tuple(typing.cast(str | bool, item) for item in values)
    return copied if isinstance(value, list) else copied[0]


def _profile_value(field: str, value: object, *, source: str, index: int) -> ProfileValue:
    if value is None:
        if field not in _OPTIONAL_FIELDS:
            _refuse(source, index, f"{field}: null is not allowed")
        return None
    if not isinstance(value, str) or not value.strip():
        _refuse(source, index, f"{field}: expects a non-empty string or null")
    if field == "skill":
        check_skill_name(value, key=f"rule {index}.skill", source=Path(source))
    elif field == "mode" and value not in DISPATCH_MODES:
        _refuse(source, index, f"mode: value must be one of {sorted(DISPATCH_MODES)}")
    elif field == "agent" and value not in _AGENTS:
        _refuse(source, index, f"agent: value must be one of {sorted(_AGENTS)}")
    return value


def parse_rules(raw: object, *, source: str, attributes: frozenset[str]) -> tuple[DispatchRule, ...]:
    """Validate and defensively copy one source's ordered dispatch rules."""
    unknown_attributes = attributes - _ATTRIBUTE_VALUES.keys()
    if unknown_attributes:
        _refuse(source, None, f"unknown declared attributes: {sorted(unknown_attributes)}")
    if not isinstance(raw, list):
        _refuse(source, None, "pipeline.rules must be a list")
    parsed: list[DispatchRule] = []
    for index, candidate in enumerate(raw):
        if not isinstance(candidate, Mapping):
            _refuse(source, index, "rule must be a mapping")
        unknown = set(candidate) - {"name", "match"} - _PROFILE_FIELDS
        if unknown:
            _refuse(source, index, f"unknown rule keys: {sorted(unknown)}")
        if "match" not in candidate or not isinstance(candidate["match"], Mapping):
            _refuse(source, index, "match is required and must be a mapping")
        name = candidate.get("name")
        if "name" in candidate and (not isinstance(name, str) or not name.strip()):
            _refuse(source, index, "name must be a non-empty string when supplied")
        raw_match = typing.cast(Mapping[object, object], candidate["match"])
        match: dict[str, str | bool | tuple[str | bool, ...]] = {}
        for attribute, value in raw_match.items():
            if not isinstance(attribute, str) or attribute not in attributes:
                _refuse(source, index, f"unknown or undeclared match attribute {attribute!r}")
            match[attribute] = _constraint(value, attribute=attribute, source=source, index=index)
        fields = {
            field: _profile_value(field, candidate[field], source=source, index=index)
            for field in _PROFILE_FIELDS
            if field in candidate
        }
        if not fields:
            _refuse(source, index, "rule must supply at least one profile field")
        parsed.append(
            DispatchRule(
                match=MappingProxyType(match),
                fields=MappingProxyType(fields),
                origin=RuleOrigin(source, index, name),
            )
        )
    return tuple(parsed)


def _matches(
    constraints: Mapping[str, str | bool | tuple[str | bool, ...]],
    attributes: Mapping[str, AttributeValue],
) -> bool:
    for attribute, constraint in constraints.items():
        actual = attributes.get(attribute)
        if actual is None:
            return False
        choices = constraint if isinstance(constraint, tuple) else (constraint,)
        if not any(type(actual) is type(choice) and actual == choice for choice in choices):
            return False
    return True


def _packaged_rule(variant: str) -> DispatchRule:
    entry = PACKAGED_PIPELINE[variant]
    index = tuple(PACKAGED_PIPELINE).index(variant)
    fields: dict[str, ProfileValue] = {
        "skill": entry.skill,
        "mode": entry.mode,
        "prompt_tail": entry.prompt_tail,
        "agent": "claude",
        "model": None,
        "reasoning_effort": None,
    }
    return DispatchRule(
        match=MappingProxyType({"variant": variant}),
        fields=MappingProxyType(fields),
        origin=RuleOrigin("packaged", index, variant),
    )


def resolve_dispatch(
    attributes: Mapping[str, AttributeValue], *, rules: tuple[DispatchRule, ...]
) -> DispatchResolution:
    """Fold packaged defaults and matching custom rules into one explained profile."""
    variant = attributes.get("variant")
    if not isinstance(variant, str) or variant not in PACKAGED_PIPELINE:
        raise WorkspaceError(f"dispatch attributes require a known variant; got {variant!r}")
    packaged = _packaged_rule(variant)
    values = dict(packaged.fields)
    origins = {
        field: FieldOrigin(packaged.origin, "explicit-null" if value is None else "set")
        for field, value in values.items()
    }
    for rule in rules:
        if not _matches(rule.match, attributes):
            continue
        fields = rule.fields
        if "agent" in fields and fields["agent"] != values["agent"]:
            for field in ("model", "reasoning_effort"):
                values[field] = None
                origins[field] = FieldOrigin(rule.origin, "agent-change")
        for field, value in fields.items():
            values[field] = value
            origins[field] = FieldOrigin(rule.origin, "explicit-null" if value is None else "set")
    if values["reasoning_effort"] is not None and values["model"] is None:
        raise DispatchProfileError("Set a model or clear reasoning_effort.")
    if values["mode"] == "relay" and values["prompt_tail"] is None:
        origin = origins["prompt_tail"].rule
        raise DispatchProfileError(
            f"{origin.source}: rule {origin.index}: relay dispatch requires a prompt_tail",
            kind="relay-untailed",
        )
    profile = DispatchProfile(
        skill=typing.cast(str, values["skill"]),
        mode=typing.cast(str, values["mode"]),
        prompt_tail=values["prompt_tail"],
        agent=typing.cast(str, values["agent"]),
        model=values["model"],
        reasoning_effort=values["reasoning_effort"],
    )
    return DispatchResolution(profile, MappingProxyType(origins))


__all__ = [
    "AttributeValue",
    "DispatchProfile",
    "DispatchProfileError",
    "DispatchResolution",
    "DispatchRule",
    "FieldOrigin",
    "ProfileValue",
    "RuleOrigin",
    "dispatch_attributes",
    "parse_rules",
    "resolve_dispatch",
]

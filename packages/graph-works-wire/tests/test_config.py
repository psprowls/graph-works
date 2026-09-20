"""The config projections reproduce `json.dumps(asdict(x), indent=2, default=str)` byte for byte.

They are written key by key so a field added upstream no longer widens the
output silently -- the field-list tests below are where such an addition
fails first, and a human decides whether the projection gains the key.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from datetime import date
from enum import IntEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType

import pytest
from config_io import ConfigEntry, Resolved
from graph_works_core.hooks import HooksResult
from graph_works_core.workspace.dispatch import DispatchRule, RuleOrigin, packaged_rules
from graph_works_core.workspace.dispatch_config import DispatchRuleSet
from graph_works_core.workspace.schema_read import SchemaRead
from graph_works_wire import config
from graph_works_wire.config import hooks_payload, projection_payload, resolved_list_payload, resolved_payload


def _legacy(value: object) -> str:
    return json.dumps(value, indent=2, default=str)


def _new(value: object) -> str:
    return json.dumps(value, indent=2)


class WireLevel(IntEnum):
    LOW = 1


class WireString(str):
    def __str__(self) -> str:
        return "custom-string"


class MisleadingInteger(int):
    def __int__(self) -> int:
        return 99


class MisleadingFloat(float):
    def __float__(self) -> float:
        return 99.0


@dataclass(frozen=True)
class NestedSetting:
    name: str
    values: tuple[object, ...]


ENTRY = ConfigEntry(
    key="workflow.auto_drive.max_parallel",
    type="int",
    default=2,
    description="How many workers.",
    env_var="GW_MAX",
    allowed=(),
    write_hint="edit by hand",
)
LIST_ENTRY = ConfigEntry(key="repos.*.tags", type="list[str]", default=["a"], description="Tags.")
ENUM_ENTRY = ConfigEntry(key="mode", type="str", default=None, description="Mode.", allowed=("a", "b"))
NESTED_ENTRY = ConfigEntry(
    key="nested",
    type="str",
    default={"items": [NestedSetting("default", (PurePosixPath("/default"), WireLevel.LOW))]},
    description="Nested compatibility fixture.",
)

RESOLVED = [
    Resolved(key="workflow.auto_drive.max_parallel", value=3, origin="manifest", entry=ENTRY),
    Resolved(key="workflow.auto_drive.max_parallel", value=4, origin="env", entry=ENTRY, shadowed=3),
    Resolved(key="repos.x.tags", value=["a", "b"], origin="local", entry=LIST_ENTRY, shadowed=None),
    Resolved(key="mode", value="a", origin="default", entry=ENUM_ENTRY),
    Resolved(key="odd", value=PurePosixPath("/p"), origin="manifest", entry=ENUM_ENTRY, shadowed={"k": ("v", 1)}),
    Resolved(
        key="nested",
        value=WireString("actual"),
        origin="manifest",
        entry=NESTED_ENTRY,
        shadowed=(NestedSetting("shadowed", (WireLevel.LOW,)),),
    ),
]


@pytest.mark.parametrize("resolved", RESOLVED, ids=lambda r: f"{r.key}-{r.origin}")
def test_resolved_payload_matches_the_asdict_encoding(resolved: Resolved) -> None:
    assert _new(resolved_payload(resolved)) == _legacy(asdict(resolved))


def test_resolved_list_payload_matches_the_asdict_encoding() -> None:
    assert _new(resolved_list_payload(RESOLVED)) == _legacy([asdict(r) for r in RESOLVED])


def test_resolved_payload_preserves_numeric_subclass_values_everywhere() -> None:
    resolved = Resolved(
        key="numeric",
        value={"nested": [MisleadingInteger(3), MisleadingFloat(2.5)]},
        origin="env",
        entry=ConfigEntry(
            key="numeric",
            type="str",
            default=(MisleadingFloat(7.5), MisleadingInteger(7)),
            description="Numeric compatibility fixture.",
        ),
        shadowed={"values": (MisleadingInteger(1), MisleadingFloat(1.5))},
    )

    payload = resolved_payload(resolved)

    entry = payload["entry"]
    assert isinstance(entry, dict)
    assert payload["value"] == {"nested": [3, 2.5]}
    assert entry["default"] == [7.5, 7]
    assert payload["shadowed"] == {"values": [1, 1.5]}
    assert _new(payload) == _legacy(asdict(resolved))


def test_resolved_payload_preserves_legacy_nan_bytes_with_nan_aware_checks() -> None:
    resolved = Resolved(
        key="nan",
        value={"nested": [float("nan")]},
        origin="env",
        entry=ConfigEntry(key="nan", type="str", default=float("nan"), description="NaN fixture."),
        shadowed=float("nan"),
    )

    payload = resolved_payload(resolved)

    value = payload["value"]
    entry = payload["entry"]
    assert isinstance(value, dict)
    assert isinstance(entry, dict)
    nested = value["nested"]
    default = entry["default"]
    shadowed = payload["shadowed"]
    assert isinstance(nested, list)
    assert isinstance(default, float)
    assert isinstance(shadowed, float)
    assert math.isnan(nested[0])
    assert math.isnan(default)
    assert math.isnan(shadowed)
    assert _new(payload) == _legacy(asdict(resolved))


@pytest.mark.parametrize(
    "result",
    [
        HooksResult(
            settings_path=Path("/r/.claude/settings.local.json"), changed=True, added=("a.sh",), skipped=("b.sh",)
        ),
        HooksResult(settings_path=Path("/r/.claude/settings.local.json"), changed=False),
    ],
)
def test_hooks_payload_matches_the_asdict_encoding(result: HooksResult) -> None:
    assert _new(hooks_payload(result)) == _legacy(asdict(result))


def test_projection_payload_matches_the_legacy_encoding() -> None:
    path = Path("/ws/.gw/config.yaml")
    assert _new(projection_payload(path)) == _legacy({"projection": path})


def test_upstream_field_lists_are_the_ones_the_projections_spell_out() -> None:
    assert [f.name for f in fields(Resolved)] == ["key", "value", "origin", "entry", "shadowed"]
    assert [f.name for f in fields(ConfigEntry)] == [
        "key",
        "type",
        "default",
        "description",
        "kind",
        "env_var",
        "allowed",
        "secret",
        "write_policy",
        "write_hint",
    ]
    assert [f.name for f in fields(HooksResult)] == ["settings_path", "changed", "added", "removed", "skipped"]


_RULE = DispatchRule(
    match=MappingProxyType({"stage": ("plan", "execute"), "has_spec": True}),
    fields=MappingProxyType({"model": "opus", "reasoning_effort": None}),
    origin=RuleOrigin("/ws/dispatch.local.yaml", 2, "mine"),
)


def test_rule_payload_renders_list_constraints_as_lists_and_scalars_as_scalars() -> None:
    assert config.rule_payload(_RULE) == {
        "match": {"stage": ["plan", "execute"], "has_spec": True},
        "fields": {"model": "opus", "reasoning_effort": None},
        "origin": {"source": "/ws/dispatch.local.yaml", "index": 2, "name": "mine"},
    }


def test_dispatch_rules_payload() -> None:
    packaged = packaged_rules()
    payload = config.dispatch_rules_payload(DispatchRuleSet(("stage", "variant"), packaged, (_RULE,)))

    assert payload["attributes"] == ["stage", "variant"]
    assert payload["packaged"] == [config.rule_payload(row) for row in packaged]
    assert payload["rules"] == [config.rule_payload(_RULE)]


def test_schema_read_payload_is_json_safe() -> None:
    read = SchemaRead(schemas={"Feature": {"type": "object"}}, sections={"_x": {"when": date(2026, 9, 19)}})

    assert config.schema_read_payload(read) == {
        "schemas": {"Feature": {"type": "object"}},
        "sections": {"_x": {"when": "2026-09-19"}},
    }

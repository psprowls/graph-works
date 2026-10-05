"""Contract-test inputs for `graph_works_wire.config`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import IntEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from config_io import ConfigEntry, Resolved
from graph_works_core.hooks import HooksResult
from graph_works_core.workspace.commits import CommitOutcome
from graph_works_core.workspace.dispatch import DispatchRule, RuleOrigin, packaged_rules
from graph_works_core.workspace.dispatch_config import DispatchRuleSet
from graph_works_core.workspace.layout import layout_for
from graph_works_core.workspace.schema_read import SchemaRead
from graph_works_core.workspace.work_schemas import SchemaRefreshPlan, SchemaRefreshResult, SchemaRefusal, SchemaWrite
from graph_works_wire import config


class _Level(IntEnum):
    LOW = 1


class _StringValue(str):
    pass


@dataclass(frozen=True)
class _NestedValue:
    label: str
    values: tuple[object, ...]


_ENTRY = ConfigEntry(
    key="k",
    type="list[str]",
    default={"nested": [_NestedValue("default", (PurePosixPath("/default"), _Level.LOW))]},
    description="d",
    allowed=(),
)
_RESOLVED = (
    Resolved(key="k", value=["a", "b"], origin="local", entry=_ENTRY, shadowed=None),
    Resolved(key="k", value=PurePosixPath("/p"), origin="env", entry=_ENTRY, shadowed=("x", 1)),
    Resolved(
        key="k",
        value=_StringValue("plain"),
        origin="manifest",
        entry=_ENTRY,
        shadowed={"nested": (_NestedValue("shadowed", (_Level.LOW,)),)},
    ),
    Resolved(key="k", value=float("nan"), origin="env", entry=_ENTRY, shadowed=None),
)

_DISPATCH_RULE = DispatchRule(
    match=MappingProxyType({"stage": ("plan",), "has_spec": True}),
    fields=MappingProxyType({"agent": "codex"}),
    origin=RuleOrigin("/ws/dispatch.yaml", 0, None),
)

_SCHEMA_PLAN = SchemaRefreshPlan(
    layout=layout_for(Path("/ws")),
    declarations_dir=Path("/ws/.gw"),
    writes=(SchemaWrite("schema/A.schema.json", Path("/ws/.gw/schema/A.schema.json"), None, b"{}", "+{}"),),
    refusals=(SchemaRefusal("schema/B.schema.json", Path("/ws/.gw/schema/B.schema.json"), "edited", "d", "-x"),),
    skipped=("schema/C.schema.json",),
    provenance=SchemaWrite("schema/provenance.json", Path("/ws/.gw/schema/provenance.json"), None, b"{}", "+{}"),
    fingerprints={},
    force=False,
    identities={},
)

_SCHEMA_COMMIT = CommitOutcome("committed", "1" * 40, "workspace: refresh schemas", ("a",), None)

CONFIG: dict[str, tuple[Callable[[], object], ...]] = {
    "config.resolved_payload": tuple(lambda r=r: config.resolved_payload(r) for r in _RESOLVED),
    "config.resolved_list_payload": (lambda: config.resolved_list_payload(_RESOLVED),),
    "config.hooks_payload": (
        lambda: config.hooks_payload(HooksResult(settings_path=Path("/s.json"), changed=True, added=("a",))),
    ),
    "config.projection_payload": (lambda: config.projection_payload(Path("/ws/.gw/config.yaml")),),
    "config.schema_refresh_payload": (
        lambda: config.schema_refresh_payload(_SCHEMA_PLAN, None),
        lambda: config.schema_refresh_payload(_SCHEMA_PLAN, SchemaRefreshResult(_SCHEMA_PLAN, ("a",), None)),
        lambda: config.schema_refresh_payload(_SCHEMA_PLAN, SchemaRefreshResult(_SCHEMA_PLAN, ("a",), _SCHEMA_COMMIT)),
        lambda: config.schema_refresh_payload(replace(_SCHEMA_PLAN, provenance=None, force=True), None),
    ),
    "config.rule_payload": (lambda: config.rule_payload(_DISPATCH_RULE),),
    "config.dispatch_rules_payload": (
        lambda: config.dispatch_rules_payload(DispatchRuleSet(("stage",), packaged_rules(), (_DISPATCH_RULE,))),
        lambda: config.dispatch_rules_payload(DispatchRuleSet((), (), ())),
    ),
    "config.schema_read_payload": (
        lambda: config.schema_read_payload(SchemaRead(schemas={"A": {"x": [1]}}, sections={"A": {"sections": []}})),
        lambda: config.schema_read_payload(SchemaRead(schemas={}, sections={})),
    ),
}

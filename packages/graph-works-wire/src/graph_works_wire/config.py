"""Plain-data projections for `gw config` results and the dispatch-rule reads.

`gw config --json` has always been `json.dumps(asdict(result), default=str)`.
These functions reproduce that output exactly -- same keys, same order, with
`jsonable` standing in for `default=str` -- but as explicit projections, so a
field added to `Resolved`, `ConfigEntry` or `HooksResult` no longer widens the
output silently (`tests/test_config.py` pins the field lists).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from config_io import ConfigEntry, Resolved
from graph_works_core.hooks import HooksResult
from graph_works_core.workspace.dispatch import DispatchRule
from graph_works_core.workspace.dispatch_config import DispatchRuleSet
from graph_works_core.workspace.schema_read import SchemaRead

from graph_works_wire._jsonable import jsonable


def _entry(entry: ConfigEntry) -> dict[str, object]:
    return {
        "key": entry.key,
        "type": entry.type,
        "default": jsonable(entry.default),
        "description": entry.description,
        "kind": entry.kind,
        "env_var": entry.env_var,
        "allowed": jsonable(entry.allowed),
        "secret": entry.secret,
        "write_policy": entry.write_policy,
        "write_hint": entry.write_hint,
    }


def resolved_payload(result: Resolved) -> dict[str, object]:
    """One effective value, its origin tier, its catalog entry, and what it shadows."""
    return {
        "key": result.key,
        "value": jsonable(result.value),
        "origin": result.origin,
        "entry": _entry(result.entry),
        "shadowed": jsonable(result.shadowed),
    }


def resolved_list_payload(results: Sequence[Resolved]) -> list[dict[str, object]]:
    """Every catalog row, in the order given."""
    return [resolved_payload(result) for result in results]


def hooks_payload(result: HooksResult) -> dict[str, object]:
    """What one `gw config hooks enable|disable` did to the settings file."""
    return {
        "settings_path": str(result.settings_path),
        "changed": result.changed,
        "added": list(result.added),
        "removed": list(result.removed),
        "skipped": list(result.skipped),
    }


def projection_payload(path: Path) -> dict[str, object]:
    """Where `gw config sync` wrote the projection."""
    return {"projection": str(path)}


def _constraint(value: object) -> object:
    """A list constraint is a tuple in core; JSON has only lists."""
    return list(value) if isinstance(value, tuple) else value


def rule_payload(rule: DispatchRule) -> dict[str, object]:
    """One dispatch rule: its match constraints, the profile fields it sets, and where it came from."""
    return {
        "match": {attribute: _constraint(value) for attribute, value in rule.match.items()},
        "fields": dict(rule.fields),
        "origin": {"source": rule.origin.source, "index": rule.origin.index, "name": rule.origin.name},
    }


def dispatch_rules_payload(ruleset: DispatchRuleSet) -> dict[str, object]:
    """`/v1/dispatch/rules`: the vocabulary, packaged rows and workspace rules in fold order."""
    return {
        "attributes": list(ruleset.attributes),
        "packaged": [rule_payload(rule) for rule in ruleset.packaged],
        "rules": [rule_payload(rule) for rule in ruleset.rules],
    }


def schema_read_payload(read: SchemaRead) -> dict[str, object]:
    """`/v1/schema`: every schema and section file, parsed as-is and keyed by stem."""
    return {"schemas": jsonable(dict(read.schemas)), "sections": jsonable(dict(read.sections))}

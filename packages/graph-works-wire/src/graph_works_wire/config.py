"""Plain-data projections for `gw config` results, written key by key in `asdict` order.

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

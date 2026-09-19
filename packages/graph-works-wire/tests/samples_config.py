"""Contract-test inputs for `graph_works_wire.config`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path, PurePosixPath

from config_io import ConfigEntry, Resolved
from graph_works_core.hooks import HooksResult
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

CONFIG: dict[str, tuple[Callable[[], object], ...]] = {
    "config.resolved_payload": tuple(lambda r=r: config.resolved_payload(r) for r in _RESOLVED),
    "config.resolved_list_payload": (lambda: config.resolved_list_payload(_RESOLVED),),
    "config.hooks_payload": (
        lambda: config.hooks_payload(HooksResult(settings_path=Path("/s.json"), changed=True, added=("a",))),
    ),
    "config.projection_payload": (lambda: config.projection_payload(Path("/ws/.gw/config.yaml")),),
}

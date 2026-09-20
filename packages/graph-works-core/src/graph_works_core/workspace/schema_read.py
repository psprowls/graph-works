"""The workspace's schema and section declaration files, parsed as-is.

No resolution: `$ref`s and `placeholder_ref`s stay as authored, because the
control plane resolves them client-side against this same raw map.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from okf_ext.bundle import SCHEMA_DIRNAME, SECTIONS_DIRNAME
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout

_YAML = YAML(typ="safe")


@dataclass(frozen=True, slots=True)
class SchemaRead:
    """Parsed `<config_dir>/schema/*.schema.json` and `<config_dir>/sections/*.yaml`, keyed by file stem."""

    schemas: Mapping[str, object]
    sections: Mapping[str, object]


def _read(directory: Path, suffix: str, parse: Callable[[bytes], object]) -> Mapping[str, object]:
    found: dict[str, object] = {}
    for path in sorted(directory.glob(f"*{suffix}")):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        try:
            found[path.name[: -len(suffix)]] = parse(raw)
        except (ValueError, YAMLError) as exc:
            raise WorkspaceError(f"{path}: cannot parse: {exc}") from exc
    return MappingProxyType(dict(sorted(found.items())))


def run_schema_read(layout: WorkspaceLayout) -> SchemaRead:
    """Read every schema and section file. Never writes.

    An absent directory reads as empty. A file that does not parse raises
    `WorkspaceError` naming it; an unreadable one propagates `OSError`.
    """
    return SchemaRead(
        schemas=_read(layout.config_dir / SCHEMA_DIRNAME, ".schema.json", json.loads),
        sections=_read(layout.config_dir / SECTIONS_DIRNAME, ".yaml", _YAML.load),
    )


__all__ = ["SchemaRead", "run_schema_read"]

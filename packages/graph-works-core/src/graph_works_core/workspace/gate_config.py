"""One repository's gate block: `repositories.<name>.gate` in `workspace.yaml`.

Read through the manifest catalog so the committed file and the local overlay
layer exactly as every other key does, then validated here: config-io checks a
value's type, this module checks what a gate needs beyond it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import resolve_checked_key

_FIELD = re.compile(r"\{[^{}]*\}")


@dataclass(frozen=True, slots=True)
class ScopedGate:
    roots: str
    command: str


@dataclass(frozen=True, slots=True)
class RepoGate:
    full: str | None
    scoped: ScopedGate | None


def _value(layout: WorkspaceLayout, key: str) -> str | None:
    value = resolve_checked_key(layout, key, environ={}).value
    if value is None:
        return None
    assert isinstance(value, str)  # `checked` refuses every stored non-string for a `str` entry
    if not value.strip():
        raise WorkspaceError(f"{layout.manifest_path}: {key}: must be a non-empty command")
    return value


def repo_gate(layout: WorkspaceLayout, repo_name: str) -> RepoGate:
    """The gate configured for *repo_name*; `RepoGate(None, None)` when none is."""
    prefix = f"repositories.{repo_name}.gate"
    full = _value(layout, f"{prefix}.full")
    roots = _value(layout, f"{prefix}.scoped.roots")
    command = _value(layout, f"{prefix}.scoped.command")
    if roots is None and command is None:
        return RepoGate(full, None)
    if command is None:
        raise WorkspaceError(f"{layout.manifest_path}: {prefix}.scoped.command: required when scoped.roots is set")
    if roots is None:
        raise WorkspaceError(f"{layout.manifest_path}: {prefix}.scoped.roots: required when scoped.command is set")
    fields = _FIELD.findall(command)
    if not fields or any(field != "{name}" for field in fields):
        raise WorkspaceError(
            f"{layout.manifest_path}: {prefix}.scoped.command: must contain {{name}} and no other brace field"
        )
    pure = PurePosixPath(roots)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise WorkspaceError(f"{layout.manifest_path}: {prefix}.scoped.roots: must be a repository-relative glob")
    return RepoGate(full, ScopedGate(roots, command))


__all__ = ["RepoGate", "ScopedGate", "repo_gate"]

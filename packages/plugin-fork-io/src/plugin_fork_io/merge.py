"""Three-way byte and permission merging; all Git execution is injected."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from .git import GitError
from .machine import Services
from .records import Conflict, SnapshotEntry


def merge_text(base: bytes, local: bytes, incoming: bytes, *, services: Services) -> tuple[bytes, bool]:
    if services.git is None:
        raise GitError("git.missing", "Text merging requires a Git runner")
    return services.git.merge_text(base, local, incoming)


def _binary(content: bytes) -> bool:
    return b"\0" in content


def merge_entry(
    path: str,
    base: SnapshotEntry | None,
    local: SnapshotEntry | None,
    incoming: SnapshotEntry | None,
    *,
    services: Services,
) -> tuple[SnapshotEntry | None, tuple[Conflict, ...]]:
    """Merge content/kind independently from mode; retain local on unresolved input."""
    conflicts: list[Conflict] = []

    def conflict(code: str) -> None:
        inputs = tuple(entry.hash if entry else None for entry in (base, local, incoming))
        modes = tuple(entry.mode if entry else None for entry in (base, local, incoming))
        kinds = tuple(entry.kind if entry else None for entry in (base, local, incoming))
        identity = hashlib.sha256(repr((path, code, inputs, modes, kinds)).encode()).hexdigest()
        conflicts.append(
            Conflict(
                identity,
                path,
                code,
                inputs[0],
                inputs[1],
                inputs[2],
                modes[0],
                modes[1],
                modes[2],
                kinds[0],
                kinds[1],
                kinds[2],
                "Explicit final path/hash or deletion required",
            )
        )

    if local == base:
        return incoming, ()
    if incoming == base or local == incoming:
        return local, ()
    if base is None or local is None or incoming is None:
        conflict("merge.add-add" if base is None else "merge.modify-delete")
        return local, tuple(conflicts)

    def value(entry: SnapshotEntry) -> tuple[str, bytes]:
        return entry.kind, entry.content

    result = local
    if value(local) == value(base):
        result = replace(incoming, mode=local.mode)
    elif value(incoming) == value(base) or value(local) == value(incoming):
        pass
    elif {base.kind, local.kind, incoming.kind} != {"file"}:
        conflict("merge.kind")
    elif any(_binary(entry.content) for entry in (base, local, incoming)):
        conflict("merge.binary")
    else:
        content, conflicted = merge_text(base.content, local.content, incoming.content, services=services)
        result = replace(local, content=content)
        if conflicted:
            conflict("merge.text")
    mode = local.mode
    if local.mode == base.mode:
        mode = incoming.mode
    elif incoming.mode != base.mode and local.mode != incoming.mode:
        conflict("merge.mode")
    return replace(result, mode=mode), tuple(conflicts)

"""`gw work gate run | wait | check`: gw runs the repository gate and records it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from graph_works_core.workspace.gate_config import ScopedGate


@dataclass(frozen=True, slots=True)
class ScopedCommand:
    command: str | None
    names: tuple[str, ...]
    uncovered: tuple[str, ...]


def expand_scoped(scoped: ScopedGate, code_paths: Sequence[str]) -> ScopedCommand:
    """One `&&`-joined command over the distinct roots the paths fall under.

    A scoped run never covers less than the item touches: any unmatched path,
    or no path at all, refuses.
    """
    pattern = PurePosixPath(scoped.roots).parts
    names: set[str] = set()
    uncovered: list[str] = []
    for path in code_paths:
        parts = PurePosixPath(path).parts
        if len(parts) >= len(pattern) and all(fnmatchcase(p, g) for p, g in zip(parts, pattern, strict=False)):
            names.add(parts[len(pattern) - 1])
        else:
            uncovered.append(path)
    if uncovered or not names:
        return ScopedCommand(None, tuple(sorted(names)), tuple(uncovered))
    ordered = tuple(sorted(names))
    return ScopedCommand(" && ".join(scoped.command.replace("{name}", n) for n in ordered), ordered, ())

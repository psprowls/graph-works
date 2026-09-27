"""What an item's `affects` declares: code paths, and the reserved workspace value.

`affects` answers one question, "what does this item write?". Its entries are
repository-relative code paths, plus one reserved value, `gw:workspace`, for
an item that changes only the graph-works workspace. Every consumer that reads
`affects` as code paths goes through `code_affects`, so the reserved value is
special-cased here once and nowhere else.

Pure and stdlib-only. The plan-file parser and drift computation live here too,
because the drift warning is a question about `affects`, not about advancing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

WORKSPACE_AFFECTS: Final = "gw:workspace"

#: Types that decompose rather than change code; their children declare `affects`.
_CONTAINER_TYPES: Final = frozenset({"Epic", "Release"})

#: A writing-plans file bullet: `- Create: `path``, `- Modify: `path:12-40` — note`, ...
_FILE_BULLET: Final = re.compile(r"^\s*-\s*(?:Create|Modify|Test|Delete):\s*`([^`]+)`")
_LINE_SUFFIX: Final = re.compile(r":\d+(?:-\d+)?$")


def code_affects(affects: Iterable[str]) -> tuple[str, ...]:
    """The entries that are code paths: everything but `gw:workspace`, in order."""
    return tuple(entry for entry in affects if entry != WORKSPACE_AFFECTS)


def touches_workspace(affects: Iterable[str]) -> bool:
    return any(entry == WORKSPACE_AFFECTS for entry in affects)


def plan_files(plan_text: str) -> tuple[str, ...]:
    """Repository paths a writing-plans artifact names in its file bullets.

    The first backticked token after `Create:`/`Modify:`/`Test:`/`Delete:`,
    with any `:<line>` or `:<start>-<end>` suffix stripped. A root-absolute
    token is a vault path and is dropped. First-seen order, no duplicates.
    """
    found: dict[str, None] = {}
    for line in plan_text.splitlines():
        match = _FILE_BULLET.match(line)
        if match is None:
            continue
        token = _LINE_SUFFIX.sub("", match.group(1).strip())
        if token and not token.startswith("/"):
            found.setdefault(token, None)
    return tuple(found)


@dataclass(frozen=True, slots=True)
class AffectsDrift:
    uncovered: tuple[str, ...]
    widening: tuple[str, ...]


def _parts(path: str) -> tuple[str, ...]:
    # The same normalisation as `orchestrate.claims._parts`: `.` and trailing
    # slashes drop, `..` is kept literally.
    return tuple(part for part in PurePosixPath(path.strip()).parts if part != "/")


def _contains(entry: tuple[str, ...], file: tuple[str, ...]) -> bool:
    return file[: len(entry)] == entry


def _shared(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
    depth = 0
    for left, right in zip(a, b, strict=False):
        if left != right:
            break
        depth += 1
    return a[:depth]


def affects_drift(files: Iterable[str], code_affects: Iterable[str]) -> AffectsDrift:
    """Files no `code_affects` entry contains, and the entries that would cover them.

    Widening per uncovered file: the deepest prefix it shares with any entry
    when that is at least two segments (`packages/graph-works-core`), else the
    file's parent directory (a top-level file widens to itself). The result is
    collapsed by containment and sorted.
    """
    entries = [_parts(entry) for entry in code_affects if entry.strip()]
    uncovered = tuple(file for file in files if not any(_contains(entry, _parts(file)) for entry in entries))
    suggestions: set[tuple[str, ...]] = set()
    for file in uncovered:
        parts = _parts(file)
        best = max((_shared(parts, entry) for entry in entries), key=len, default=())
        suggestions.add(best if len(best) >= 2 else (parts[:-1] or parts))
    collapsed = {s for s in suggestions if not any(o != s and _contains(o, s) for o in suggestions)}
    return AffectsDrift(uncovered, tuple(sorted("/".join(s) for s in collapsed)))


def needs_affects_hint(type_: str, *, has_parent: bool, has_children: bool, affects: Iterable[str]) -> bool:
    """A nested leaf that is not a container and declares nothing, not even `gw:workspace`."""
    return (
        has_parent
        and not has_children
        and type_ not in _CONTAINER_TYPES
        and not any(entry.strip() for entry in affects)
    )


__all__ = [
    "WORKSPACE_AFFECTS",
    "AffectsDrift",
    "affects_drift",
    "code_affects",
    "needs_affects_hint",
    "plan_files",
    "touches_workspace",
]

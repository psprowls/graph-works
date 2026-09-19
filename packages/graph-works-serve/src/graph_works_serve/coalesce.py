"""Pure coalescing for the change stream: one debounce window -> one classified batch.

`watchfiles` yields an unordered set per window, so the net change per file
cannot come from event order. The disk at flush time decides instead
(`exists` is injected; `watch.py` stats). Raw changes arrive as
`(change name, path)`, so this module never imports `watchfiles`:
`watchfiles.Change` is an IntEnum whose *names* are core's `Change` values.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import PurePath

from graph_works_core.events import Change, ChangeEvent

RawChange = tuple[str, str]
ClassifyFn = Callable[[PurePath, Change], ChangeEvent | None]


def net_changes(raw: Iterable[RawChange], exists: Callable[[str], bool]) -> list[tuple[Change, PurePath]]:
    """Present with only `added` seen -> added; present otherwise -> modified; absent -> deleted."""
    seen: dict[str, set[Change]] = {}
    for name, path in raw:
        seen.setdefault(path, set()).add(Change(name))
    net: list[tuple[Change, PurePath]] = []
    for path in sorted(seen):
        if not exists(path):
            change = Change.DELETED
        elif seen[path] == {Change.ADDED}:
            change = Change.ADDED
        else:
            change = Change.MODIFIED
        net.append((change, PurePath(path)))
    return net


def _identity(event: ChangeEvent) -> tuple[str, str, str]:
    return (event.kind.value, event.path, event.member or "")


def build_batch(net: Iterable[tuple[Change, PurePath]], classify_fn: ClassifyFn) -> tuple[ChangeEvent, ...]:
    """Classify each net change, drop `None`, de-duplicate and sort on `(kind, path, member)`."""
    unique: dict[tuple[str, str, str], ChangeEvent] = {}
    for change, path in net:
        event = classify_fn(path, change)
        if event is not None:
            unique.setdefault(_identity(event), event)
    return tuple(unique[key] for key in sorted(unique))


__all__ = ["ClassifyFn", "RawChange", "build_batch", "net_changes"]

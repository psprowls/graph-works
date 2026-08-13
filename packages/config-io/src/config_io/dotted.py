"""`get` / `set_in` / `unset_in` over nested mappings addressed by dotted key.

The public successor to the seed's `_get_path` / `_set_path` / `_unset_path`.
Named for what it operates on — dotted keys over nested mappings — rather than
`paths`, which means filesystem layout everywhere else in this workspace.

Reads are total: no shape of data and no shape of key raises. A key that runs
past a scalar, or through a missing branch, is simply absent.
"""

from __future__ import annotations

from collections.abc import Mapping


def get(data: Mapping[str, object], key: str) -> object | None:
    """The value at `key`, or None if any segment is missing or not a mapping."""
    node: object = data
    for seg in key.split("."):
        if not isinstance(node, Mapping) or seg not in node:
            return None
        node = node[seg]
    return node


def set_in(data: dict[str, object], key: str, value: object) -> None:
    """Write `value` at `key`, creating (or replacing) intermediate mappings."""
    segs = key.split(".")
    node: dict[str, object] = data
    for seg in segs[:-1]:
        child: dict[str, object]
        existing = node.get(seg)
        if isinstance(existing, dict):
            child = existing
        else:
            child = {}
            node[seg] = child
        node = child
    node[segs[-1]] = value


def unset_in(data: dict[str, object], key: str) -> bool:
    """Remove `key`; return whether anything was removed.

    Parent mappings left empty by the removal are pruned bottom-up, so a
    store's omit-when-empty guards see clean absence rather than a husk.
    """
    segs = key.split(".")
    parents: list[tuple[dict[str, object], str]] = []
    node: dict[str, object] = data
    for seg in segs[:-1]:
        existing = node.get(seg)
        if not isinstance(existing, dict):
            return False
        parents.append((node, seg))
        node = existing
    if segs[-1] not in node:
        return False
    del node[segs[-1]]
    for parent, seg in reversed(parents):
        if not parent[seg]:
            del parent[seg]
    return True

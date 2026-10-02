"""An immutable, indexed work projection: one index build per read pass (D-007).

`WorkSnapshot` *is* a `Sequence[WorkItem]`, so every helper that takes a
sequence keeps its signature and starts with `as_snapshot(items)`: identity
for a snapshot, one O(n) build otherwise -- the cost a single call paid
before. Hot loops build one snapshot up front and pass it through.

`WorkSnapshot(items)` wraps *items* exactly as given, `child_paths` included:
hand-built projections may disagree with their own `parent_path`, and every
helper must answer for them exactly as it did. Sorting and filling
`child_paths` is the loaders' job (`from_bundle`, `from_rows`).

A snapshot lives as long as the `load_items` (or `routing_items`) result that
produced it. Nothing caches one across calls or across a write.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Any, cast, overload

from okf_io import Bundle, build_frontmatter

from work_tracker_okf.items import WorkItem, _project
from work_tracker_okf.paths import parse_item_path


def _build_indexes(
    items: tuple[WorkItem, ...],
) -> tuple[Mapping[str, WorkItem], Mapping[str, tuple[WorkItem, ...]]]:
    """`(by_path, by_parent)`. Called exactly once per snapshot; last path wins."""
    by_path: dict[str, WorkItem] = {}
    grouped: dict[str, list[WorkItem]] = {}
    for item in items:
        by_path[item.path] = item
        if item.parent_path:
            grouped.setdefault(item.parent_path, []).append(item)
    return (
        MappingProxyType(by_path),
        MappingProxyType({parent: tuple(children) for parent, children in grouped.items()}),
    )


def _link(projected: Iterable[WorkItem]) -> tuple[WorkItem, ...]:
    """Path-sort and fill `child_paths` from `parent_path`: the `load_items` contract."""
    listed = list(projected)
    children: dict[str, list[str]] = {}
    for item in listed:
        if item.parent_path is not None:
            children.setdefault(item.parent_path, []).append(item.path)
    return tuple(
        replace(item, child_paths=tuple(sorted(children.get(item.path, ()))))
        for item in sorted(listed, key=lambda item: item.path)
    )


class WorkSnapshot(Sequence[WorkItem]):
    """Immutable `Sequence[WorkItem]` with eager indexes and a per-instance memo.

    `by_path` maps each permanent path to its item (last wins, as
    `path_index` always did). `by_parent` groups items by `parent_path` in
    sequence order (the frontier's view). `child_items(path)` resolves the
    item's own `child_paths` (the hierarchy helpers' view). For a loaded
    snapshot the two child views agree.

    `memo` stores immutable values only, so no read can change what a later
    read sees. A racing duplicate computation is harmless: both results are equal.
    """

    __slots__ = ("_items", "_memo", "by_parent", "by_path")

    _items: tuple[WorkItem, ...]
    by_path: Mapping[str, WorkItem]
    by_parent: Mapping[str, tuple[WorkItem, ...]]
    _memo: dict[tuple[str, str], object]

    def __init__(self, items: Iterable[WorkItem]) -> None:
        frozen = tuple(items)
        by_path, by_parent = _build_indexes(frozen)
        object.__setattr__(self, "_items", frozen)
        object.__setattr__(self, "by_path", by_path)
        object.__setattr__(self, "by_parent", by_parent)
        object.__setattr__(self, "_memo", {})

    @classmethod
    def from_bundle(cls, bundle: Bundle) -> WorkSnapshot:
        """Every concept whose extensionless id passes `parse_item_path`.

        Preserve the document's typed tags and sources, including native YAML
        values in source extras and the v0.1 body-citation fallback. These can
        differ from the plain-row reconstruction described by `from_rows`.
        """
        projected: list[WorkItem] = []
        for concept_id, document in bundle.concepts.items():
            location = parse_item_path(concept_id)
            if location is not None:
                fm = document.fm
                projected.append(_project(location, document.fm_data(dates="iso"), tags=fm.tags, sources=fm.sources))
        return cls(_link(projected))

    @classmethod
    def from_rows(cls, rows: Iterable[tuple[str, Mapping[str, Any]]]) -> WorkSnapshot:
        """Index rows: `(concept_id, fm_data(dates="iso"))` pairs (D-008).

        Tags and sources come from `build_frontmatter(data)` with no body, so
        a v0.1 page citing only in its body projects empty `sources`; the
        `legacy` lint already reports such pages.

        Native YAML dates also differ: `fm_data(dates="iso")` converts them
        to ISO strings, indistinguishable from identical authored strings.
        Reconstruction accepts these strings in tags and Source scalar fields
        (id, resource, title), where the bundle's typed view rejects native
        dates, and retains strings in Source.extra, including nested values,
        where the bundle retains native dates.

        The plain-data conversion also stringifies mapping keys recursively,
        including mappings nested in Source.extra lists. Distinct native keys
        with the same string representation collapse before reconstruction;
        the last entry in mapping iteration order wins (D-013). Bundle source
        extras retain their nested native keys and all colliding entries.
        These, native-date conversion and omitted body citations are accepted
        projection differences; bundle values and the plain row format remain
        unchanged. Rows cannot recover information lost during conversion.
        """
        projected: list[WorkItem] = []
        for concept_id, data in rows:
            location = parse_item_path(concept_id)
            if location is not None:
                fm = build_frontmatter(data)
                projected.append(_project(location, data, tags=fm.tags, sources=fm.sources))
        return cls(_link(projected))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"WorkSnapshot is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"WorkSnapshot is immutable; cannot delete {name!r}")

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[WorkItem]:
        return iter(self._items)

    @overload
    def __getitem__(self, index: int) -> WorkItem: ...
    @overload
    def __getitem__(self, index: slice) -> tuple[WorkItem, ...]: ...
    def __getitem__(self, index: int | slice) -> WorkItem | tuple[WorkItem, ...]:
        return self._items[index]

    def __repr__(self) -> str:
        return f"WorkSnapshot(<{len(self._items)} items>)"

    def child_items(self, path: str) -> tuple[WorkItem, ...]:
        """*path*'s children in its own `child_paths` order; unknown children skipped."""

        def compute() -> tuple[WorkItem, ...]:
            parent = self.by_path.get(path)
            if parent is None:
                return ()
            return tuple(self.by_path[child] for child in parent.child_paths if child in self.by_path)

        return self.memo("child_items", path, compute)

    def memo[T](self, kind: str, key: str, compute: Callable[[], T]) -> T:
        """`compute()` once per `(kind, key)` for this snapshot's lifetime."""
        slot = (kind, key)
        if slot not in self._memo:
            self._memo[slot] = compute()
        return cast(T, self._memo[slot])


def as_snapshot(items: Sequence[WorkItem]) -> WorkSnapshot:
    """*items* itself when it is a snapshot; otherwise one build over it, as given."""
    return items if isinstance(items, WorkSnapshot) else WorkSnapshot(items)


__all__ = ["WorkSnapshot", "as_snapshot"]

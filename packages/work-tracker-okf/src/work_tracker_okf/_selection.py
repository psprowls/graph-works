"""Selection helpers for path-keyed projections."""

from collections.abc import Iterable
from typing import Protocol, cast


class PathProjection(Protocol):
    @property
    def path(self) -> str: ...


def path_index[Projection: PathProjection](items: Iterable[Projection]) -> dict[str, Projection]:
    """Index distinct permanent paths without archive-twin preference."""
    from work_tracker_okf.snapshot import WorkSnapshot

    if isinstance(items, WorkSnapshot):
        return cast(dict[str, Projection], dict(items.by_path))
    return {item.path: item for item in items}


__all__ = ["PathProjection", "path_index"]

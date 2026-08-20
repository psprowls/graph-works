"""Shared selection rules for projections that can contain archive twins."""

from collections.abc import Iterable
from typing import Protocol


class SluggedProjection(Protocol):
    @property
    def slug(self) -> str: ...

    @property
    def archived(self) -> bool: ...


def active_preferred_slug_index[Projection: SluggedProjection](
    items: Iterable[Projection],
) -> dict[str, Projection]:
    """Index by slug, retaining an archived projection only without an active one."""
    selected: dict[str, Projection] = {}
    for item in items:
        current = selected.get(item.slug)
        if current is None or (current.archived and not item.archived):
            selected[item.slug] = item
    return selected

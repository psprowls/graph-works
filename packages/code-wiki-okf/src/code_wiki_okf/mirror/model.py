"""Frozen values `plan_mirror` returns and `apply_mirror` consumes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from okf_ext.generators import Render
from okf_ext.moves import MovePlan
from okf_io import IndexUpdate

DeclineReason = Literal["prose-edited"]


@dataclass(frozen=True, slots=True)
class DeclinedDeletion:
    resource: str
    path: str  # bundle-relative posix, e.g. "repositories/acme/src/pkg/base.py.md"
    reason: DeclineReason


@dataclass(frozen=True, slots=True)
class MirrorPlan:
    """A preview: what one repo's sync run would create, update, move, and
    delete. Nothing here has been written yet.

    `creates` and `updates` carry renders, not a `RegenerationPlan` -- a
    create is not yet a bundle member for `plan_regenerate` to accept, so
    that call has to happen later, after moves and creates have landed and
    the bundle has been reloaded (a later task's job, not this one's).
    """

    repo: str
    moves: MovePlan
    creates: Mapping[str, tuple[dict[str, Any], Render]]  # rel_path -> (frontmatter, Render)
    updates: Mapping[str, Render]  # concept_id -> Render, rich pages only
    deletions: tuple[str, ...]  # rel_paths, prose-guard passed
    declined_deletions: tuple[DeclinedDeletion, ...]

    @property
    def is_empty(self) -> bool:
        return self.moves.is_empty and not self.creates and not self.updates and not self.deletions


@dataclass(frozen=True, slots=True)
class MirrorResult:
    """What one `apply_mirror` call actually did.

    `regenerated` names concept ids -- updates and freshly-created rich pages
    both land here, since both went through the same regeneration batch.
    `index_updates` is scoped to this repo's own `repositories/<repo>/**`
    subtree; it never touches another repo's mirror or the bundle's other
    lanes.
    """

    repo: str
    moved: tuple[tuple[str, str], ...]
    created: tuple[str, ...]  # rel_paths
    regenerated: tuple[str, ...]  # concept_ids
    deleted: tuple[str, ...]  # rel_paths
    declined_deletions: tuple[DeclinedDeletion, ...]
    index_updates: tuple[IndexUpdate, ...]


__all__ = ["DeclineReason", "DeclinedDeletion", "MirrorPlan", "MirrorResult"]

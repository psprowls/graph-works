"""Frozen values `plan_mirror` returns and `apply_mirror` consumes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from okf_ext.generators import Render
from okf_ext.moves import MovePlan
from okf_io import IndexUpdate

DeclineReason = Literal["prose-edited"]


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _freeze_render(render: Render) -> Render:
    return Render(
        frontmatter=MappingProxyType({key: _freeze(value) for key, value in render.frontmatter.items()}),
        sections=MappingProxyType(dict(render.sections)),
    )


def _freeze_move_plan(plan: MovePlan) -> MovePlan:
    """Copy the extension plan so no mutable caller-owned collection leaks in."""
    return MovePlan(
        root=plan.root,
        moves=tuple(plan.moves),
        edits=tuple(plan.edits),
        refusals=tuple(plan.refusals),
        unrebased=tuple(plan.unrebased),
        digests=MappingProxyType(dict(plan.digests)),
        relocate=plan.relocate,
        stranded=tuple(plan.stranded),
    )


@dataclass(frozen=True, slots=True)
class MirrorTarget:
    """One graph File's immutable identity and canonical bundle member."""

    resource: str
    source_path: str
    member: str


@dataclass(frozen=True, slots=True)
class DeclinedDeletion:
    resource: str
    path: str  # bundle-relative posix, e.g. "code-graph/acme/file-system/src/pkg/base.py.md"
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
    targets: tuple[MirrorTarget, ...]
    moves: MovePlan
    creates: Mapping[str, tuple[Mapping[str, Any], Render]]  # source_path -> planned page parts
    updates: Mapping[str, Render]  # concept_id -> Render, rich pages only
    deletions: tuple[str, ...]  # rel_paths, prose-guard passed
    declined_deletions: tuple[DeclinedDeletion, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "targets", tuple(self.targets))
        object.__setattr__(self, "moves", _freeze_move_plan(self.moves))
        object.__setattr__(
            self,
            "creates",
            MappingProxyType(
                {
                    source_path: (
                        MappingProxyType({key: _freeze(value) for key, value in frontmatter.items()}),
                        _freeze_render(render),
                    )
                    for source_path, (frontmatter, render) in self.creates.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "updates",
            MappingProxyType({concept_id: _freeze_render(render) for concept_id, render in self.updates.items()}),
        )
        object.__setattr__(self, "deletions", tuple(self.deletions))
        object.__setattr__(self, "declined_deletions", tuple(self.declined_deletions))

    def target_for(self, source_path: str) -> MirrorTarget:
        for target in self.targets:
            if target.source_path == source_path:
                return target
        raise KeyError(source_path)

    @property
    def is_empty(self) -> bool:
        return self.moves.is_empty and not self.creates and not self.updates and not self.deletions


@dataclass(frozen=True, slots=True)
class MirrorResult:
    """What one `apply_mirror` call actually did.

    `regenerated` names concept ids -- updates and freshly-created rich pages
    both land here, since both went through the same regeneration batch.
    `index_updates` is scoped to this repo's own `code-graph/<repo>/**`
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
    failed: tuple[str, ...] = ()


__all__ = ["DeclineReason", "DeclinedDeletion", "MirrorPlan", "MirrorResult", "MirrorTarget"]

"""Frozen values the sections capability returns.

Frozen, slotted and `MappingProxyType`-backed, matching `okf_ext.tags.model`,
`okf_ext.schemas.model` and the core.

**One declaration, two readers.** `SectionSpec` and `TypeSections` are what
`section_rule` measures a document against *and* what `render_skeleton` and
`plan_sections` write from -- the move `okf_io.migrate` makes with
`doc.fm.fallbacks`, so reader, validator and writer cannot disagree about what
counts. Both now live in `okf_ext.shape`, because `okf_ext.generators` is a
third reader and a capability may not import a sibling. `SectionSet` and
`SectionSpec` are re-exported here too, so a caller that reached into this
submodule directly -- rather than through `okf_ext.sections` -- keeps
working for the same one-minor-version grace period the package-level shim
grants.

This module imports the shared layer and nothing else from its own package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from okf_ext.shape import SectionSet as SectionSet
from okf_ext.shape import SectionSpec as SectionSpec
from okf_ext.writing import Skipped


@dataclass(frozen=True, slots=True)
class SectionInsert:
    """One scaffolded section. `line` is the first line of the whole inserted
    block, **including the separating blank when one was needed** -- so a
    caller diffing from `line` sees a contiguous span with no gap-shaped hole
    above it. `tables`' `TextSplice.line` means the same thing."""

    heading: str
    line: int  # 1-based, body-relative


@dataclass(frozen=True, slots=True)
class SectionSplice:
    """One document's whole scaffold, ready to write.

    **One splice per document, not per section**, so a document missing three
    sections is one write. `after` carries the whole new body, which makes
    `apply` a pure writer -- digest-check, then write -- with no chance of the
    plan and the apply disagreeing about what an insert means. That is
    `RowSplice`'s stated reason for the same choice.

    `digest` is over the **whole** body, for `RowSplice`'s other stated
    reason: a change anywhere can move the line an insert lands on, so "the
    section list still looks the same" is a weaker check than it appears.
    """

    concept_id: str
    path: str  # bundle-relative posix
    inserts: tuple[SectionInsert, ...]  # document order
    digest: str
    after: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SectionPlan:
    """A preview you can inspect and filter before anything is written.

    A value rather than a `dry_run=True` flag, for the reason `RenamePlan`
    gives and `SplicePlan` repeats: "apply 38 of these 40" is a thing a
    boolean cannot express. **Idempotence surfaces as an empty plan** -- a
    document already carrying every required section produces no splice, and
    `is_empty` is the signal.
    """

    root: Path  # the bundle this was planned against
    splices: tuple[SectionSplice, ...]
    skipped: tuple[Skipped, ...]

    @property
    def is_empty(self) -> bool:
        return not self.splices

    @property
    def concept_ids(self) -> tuple[str, ...]:
        return tuple(sorted({splice.concept_id for splice in self.splices}))

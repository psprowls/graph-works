"""Frozen values the sections capability returns.

Frozen, slotted and `MappingProxyType`-backed, matching `okf_ext.tags.model`,
`okf_ext.schemas.model` and the core.

**One declaration, two readers.** `SectionSpec` and `TypeSections` are what
`section_rule` measures a document against *and* what `render_skeleton` and
`plan_sections` write from -- the move `okf_io.migrate` makes with
`doc.fm.fallbacks`, so reader, validator and writer cannot disagree about what
counts.

This module imports the shared layer and nothing else from its own package.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from okf_ext.writing import Skipped


class SectionError(ValueError):
    """A declaration set the caller got wrong.

    Caller **configuration** is always an exception; bundle **content** never
    is. One complaint at load beats the same confusion repeated against every
    concept in the bundle.

    Subclasses `ValueError` so a caller catching either works, mirroring
    `okf_ext.tags.VocabularyError` and `okf_ext.schemas.SchemaError`.
    """


@dataclass(frozen=True, slots=True)
class SectionSpec:
    """One declared section.

    `placeholder` is **already resolved**: no `placeholder_ref` survives
    loading, which is what lets `render_skeleton` be a pure function of its
    argument with no registry to consult.
    """

    heading: str
    level: int = 2
    required: bool = False
    seeded_is_complete: bool = False
    placeholder: str = ""


@dataclass(frozen=True, slots=True)
class TypeSections:
    """What one `type` declares. `sections` is in declaration order, which is
    what decides where an insert lands -- and nothing else (spec §5.4:
    reordering sections is house style, not a defect)."""

    sections: tuple[SectionSpec, ...]
    additional_sections: bool = True


@dataclass(frozen=True, slots=True)
class SectionSet:
    """A directory of declarations, read once.

    `types` is the dispatch table; `sources` answers "what says so" so a
    `Finding` can cite `feature.yaml` by name -- the move `Vocabulary.source`
    and `SchemaSet.sources` both make. `fragments` is the union of every
    `fragments:` mapping in the directory, `_`-prefixed files included, and is
    kept after loading so a caller can see what a `placeholder_ref` resolved
    to.
    """

    types: Mapping[str, TypeSections]  # type name -> declaration
    sources: Mapping[str, str]  # type name -> filename
    fragments: Mapping[str, str]  # fragment name -> text
    root: Path  # the directory this was read from

    @property
    def type_names(self) -> tuple[str, ...]:
        """Named `type_names` rather than `types` -- unlike `SchemaSet.types`,
        which is the property, `types` here is the mapping field the spec
        names."""
        return tuple(sorted(self.types))


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

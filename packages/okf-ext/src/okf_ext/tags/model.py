"""Frozen values the tags capability returns.

All frozen and slotted, matching the core.

`Skipped`, `SkipReason`, `WriteFailure`, `FailureKind` and `ApplyResult` are
**re-exported from `okf_ext.writing`**, not defined here. They moved to the
shared layer when `tables` needed the same write engine -- the independence
contract forbids one capability importing them from another. They stay
importable from `okf_ext.tags` so this capability's public surface is
unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from okf_ext.writing import ApplyResult, FailureKind, Skipped, SkipReason, WriteFailure


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """A controlled tag vocabulary — a **house rule**, never a spec claim.

    `allowed` and `deprecated` are disjoint: a deprecated tag is *known* but
    not allowed, which is what lets `tags.deprecated` and `tags.unknown` mean
    different things.
    """

    allowed: frozenset[str]
    deprecated: Mapping[str, str | None]  # tag -> replacement, or None
    descriptions: Mapping[str, str]
    source: str  # what says so — cited as `Finding.spec`

    @property
    def known(self) -> frozenset[str]:
        return self.allowed | frozenset(self.deprecated)


@dataclass(frozen=True, slots=True)
class TagDefinition:
    """One tag a package contributes.

    Distinct from `Vocabulary`, which is the whole file. A package declares
    these; it never ships `tags.yaml` content -- the vocabulary is the
    vault's, and `plan_install` refuses the file as a whole-file member for
    exactly that reason.

    `description` is required rather than optional even though the file
    format allows an entry without one: a contributed tag with no description
    is a tag a human meets in a finding with nothing to explain it.
    """

    name: str
    description: str
    deprecated: bool = False
    replaced_by: str | None = None


@dataclass(frozen=True, slots=True)
class TagDrift:
    """A contributed tag whose description the human has since changed.

    Reported, never overwritten. `description` is prose the human owns, the
    same claim ADR-0009 makes about an index entry's text; only `deprecated`
    and `replaced_by` are machine instructions, and only those refuse.
    """

    name: str
    ours: str  # the package's description
    theirs: str  # what the file says now -- `""` when the entry carries none


@dataclass(frozen=True, slots=True)
class VocabularyPlan:
    """A preview of merging one package's definitions into a vault's file.

    A value rather than a `dry_run=True` flag, following every other planner
    in this package (ADR-0022). `before` and `after` are the whole file, so a
    caller can diff them without re-reading anything.

    On a whole-merge refusal -- a malformed file, or one whose `tags:` block
    the locator cannot anchor -- `after == before`, `added` is empty and the
    reason is the single entry in `refusals`.
    """

    path: Path
    before: str
    after: str
    added: tuple[str, ...]  # names this merge would introduce, in definition order
    unchanged: tuple[str, ...]  # names already present and identical
    drift: tuple[TagDrift, ...]
    refusals: tuple[WriteFailure, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def is_empty(self) -> bool:
        return not self.added


@dataclass(frozen=True, slots=True)
class TagInventory:
    """What the bundle actually carries. Reports the mess; never fixes it."""

    counts: Mapping[str, int]  # tag -> number of concepts carrying it
    concepts: Mapping[str, tuple[str, ...]]  # tag -> sorted concept ids
    untagged: tuple[str, ...]  # sorted concept ids carrying no tags
    co_occurrence: Mapping[tuple[str, str], int]  # sorted pair -> shared concepts
    skipped: tuple[Skipped, ...]

    @property
    def tags(self) -> tuple[str, ...]:
        return tuple(sorted(self.counts))

    def pairs(self, *, minimum: int = 1) -> tuple[tuple[str, str, int], ...]:
        """Co-occurrence as `(tag, tag, count)`, commonest first.

        Ties break lexicographically rather than by dict order, so the same
        bundle produces the same list on every machine.
        """
        return tuple(
            (left, right, count)
            for (left, right), count in sorted(self.co_occurrence.items(), key=lambda item: (-item[1], item[0]))
            if count >= minimum
        )


@dataclass(frozen=True, slots=True)
class TagCluster:
    """Tags that may be the same tag, at one of two **very** different
    confidences.

    `normalization` members share a canonical form. Mechanical, safe to apply
    blind. `similarity` members merely look alike; that is a suggestion for a
    human and is **never** auto-applied. Collapsing the two into one
    "these look alike" list is the mistake this design exists to avoid.

    Every `members` entry, of either kind, is a real tag the bundle actually
    carries — never a bare canonical form nobody wrote. The two kinds are
    not otherwise guaranteed disjoint: a `normalization` cluster's canonical
    form, when it is itself one of that group's spellings, may also head a
    `similarity` cluster with an unrelated lookalike (see
    `okf_ext.tags.inventory.clusters`).

    To turn a cluster into a merge plan: `plan_merge(bundle, [m for m in
    cluster.members if m != cluster.canonical], cluster.canonical)`.
    """

    canonical: str
    members: tuple[str, ...]
    kind: Literal["normalization", "similarity"]
    score: float | None = None  # None when kind == "normalization"


@dataclass(frozen=True, slots=True)
class TagEdit:
    """One tag position to rewrite. `new is None` means remove the position."""

    concept_id: str
    path: str  # bundle-relative posix
    index: int  # position in the raw tags sequence
    old: str
    new: str | None


@dataclass(frozen=True, slots=True)
class RenamePlan:
    """A preview you can inspect and filter before anything is written.

    A value rather than a `dry_run=True` flag, deliberately: "apply 38 of these
    40" is a thing a boolean cannot express.

    **Consuming `edits` safely.** They are ordered `(concept_id, index)`
    ascending -- for readability and so two planning runs compare equal --
    not for application order. A consumer must group edits by `concept_id`
    and, within each group, either delete positions in *descending* index
    order or rebuild the tag sequence from scratch rather than mutating it
    in place. Deleting ascending against a shrinking list is wrong: for
    `tags=['kpi', 'metric', 'metrics']` with edits
    `[(0, 'kpi', None), (2, 'metrics', None)]` (this shape is the corpus's
    `merge_me`), deleting index 0 first shifts `metrics` down to index 1,
    so the still-pending "delete index 2" removes the wrong element or
    raises `IndexError` outright. Deleting 2 then 0 -- or any consumer that
    rebuilds the sequence instead of mutating it -- leaves exactly `['metric']`.
    """

    root: Path  # the bundle this was planned against
    edits: tuple[TagEdit, ...]
    skipped: tuple[Skipped, ...]

    @property
    def is_empty(self) -> bool:
        return not self.edits

    @property
    def concept_ids(self) -> tuple[str, ...]:
        return tuple(sorted({edit.concept_id for edit in self.edits}))


__all__ = [
    "ApplyResult",
    "FailureKind",
    "RenamePlan",
    "SkipReason",
    "Skipped",
    "TagCluster",
    "TagDefinition",
    "TagDrift",
    "TagEdit",
    "TagInventory",
    "Vocabulary",
    "VocabularyPlan",
    "WriteFailure",
]

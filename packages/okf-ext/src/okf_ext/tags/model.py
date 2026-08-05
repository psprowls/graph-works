"""Frozen values the tags capability returns.

All frozen and slotted, matching the core. `WriteFailure` stores a rendered
message rather than a live exception so every one of these survives
`json.dumps` — the habit `fm_data(dates="iso")` and `Severity`-as-`Literal`
established in okf-io.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

#: Why a member could not be considered. Content is never an exception (spec
#: §10) — it is a `Finding` or one of these.
SkipReason = Literal["parse-error", "tags-not-a-sequence", "unreadable"]

#: Why one document did not land, machine-readable rather than substring-
#: matched out of `WriteFailure.error`. Mirrors `apply()`'s three regimes:
#: `not-a-member`, `parse-error`, `tags-not-a-sequence`, `duplicate-edit`, and
#: `stale` are content failures, refused for that document alone; `unwritable`
#: and `stage-error` are the all-or-nothing probe/staging regime; `commit-error`
#: is the per-document commit regime. `serialize-error` is content-shaped
#: (isolated per document) but named separately from the other content kinds
#: because it is raised by the document itself, not detected by `apply()`.
#: `stale` is the one a caller can act on by re-planning; `unwritable`,
#: `stage-error`, and `commit-error` are the ones worth retrying as-is.
FailureKind = Literal[
    "not-a-member",
    "parse-error",
    "tags-not-a-sequence",
    "duplicate-edit",
    "stale",
    "serialize-error",
    "unwritable",
    "stage-error",
    "commit-error",
]


@dataclass(frozen=True, slots=True)
class Skipped:
    """A member the tag functions could not read, and why."""

    concept_id: str
    path: str  # bundle-relative posix
    reason: SkipReason
    detail: str


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
            for (left, right), count in sorted(
                self.co_occurrence.items(), key=lambda item: (-item[1], item[0])
            )
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


@dataclass(frozen=True, slots=True)
class WriteFailure:
    """A document that did not land, the rendered reason, and its `kind`.

    `kind` is the machine-readable discriminator; `error` stays the rendered
    prose. A caller that wants to retry an I/O failure but re-plan on a stale
    one needs `kind`, not a substring match against `error` — see `FailureKind`.
    """

    path: str
    kind: FailureKind
    error: str  # str(exc) — the message, not the live exception


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """What landed, what did not, and what was never considered."""

    written: tuple[str, ...]
    failed: tuple[WriteFailure, ...]
    skipped: tuple[Skipped, ...]  # carried from the plan

    @property
    def ok(self) -> bool:
        return not self.failed

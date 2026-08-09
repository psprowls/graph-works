"""Frozen values the proposals capability passes between its stages.

All frozen and slotted, `Mapping` defaults `MappingProxyType`-backed, matching
`okf_ext.moves.model`, `okf_ext.generators.model` and the core. `WriteFailure`,
`FailureKind`, `ApplyResult` and `Skipped` are **not** here: they come from
`okf_ext.writing`, the shared layer, so a caller discriminating a write failure
has one type to match rather than one per capability.

`sources` and `verified` are carried as **plain data** -- the mappings
`Document.fm_data()` returns -- rather than as `okf_io.models.Source` and
`Verified`. That is what makes "promotion copies `sources[]` verbatim" true for
every key including the ones OKF does not name: re-typing through `Source` and
back would have to re-emit each typed field and would lose `extra` ordering.
The side effect is that every value here survives `json.dumps` with no encoder,
which is the promise `fm_data` already makes.

This module imports stdlib only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

#: The `type` this capability owns outright. Enumeration is
#: `Bundle.by_type(PROPOSAL_TYPE)`: a member's id in OKF is its path, so
#: identity is the `target`, and the file's own location is placement only.
PROPOSAL_TYPE = "Proposal"

#: The state of the page being argued for -- **not** OKF `status`, which
#: describes the document carrying it, and not `workflow_status`, which would
#: collide with the work lane's key. A fixed, closed vocabulary: a caller
#: wanting different states wants a different capability.
PageStatus = Literal["proposed", "approved", "rejected", "created"]

#: The same four at runtime, in **ledger order** rather than alphabetical:
#: `proposed` -> decided -> `created` is the transition sequence, and reading
#: it in that order is what makes the two legal transitions obvious.
PAGE_STATUSES: tuple[PageStatus, ...] = ("proposed", "approved", "rejected", "created")

#: Which of the two decisions a `plan_decide` records.
Decision = Literal["approved", "rejected"]

#: Whether a promotion (or a proposal's own read) is bringing a page into
#: being or editing one that is already there. **Derived, never stored** --
#: nothing that is not written down can drift from reality.
Mode = Literal["create", "update"]

#: Why one plan will not be applied. A closed vocabulary: every refusal a
#: planner can produce is one of these, and any refusal makes the whole plan
#: not `ok`.
#:
#: `unreadable-target` is a deliberate extension beyond the design spec's
#: seven, in the same spirit as `moves`' `reserved-dest`. `plan_promote` in
#: update mode merges `sources[]` onto an existing page; a page that failed to
#: parse has `fm_raw == {}`, so the merge would compute against frontmatter the
#: reader could not see and silently overwrite it. Refusing at plan time is the
#: only place that can be caught before a preview claims a write it should not
#: make -- `apply`'s own `parse-error` guard fires too late to keep the plan
#: honest.
RefusalKind = Literal[
    "already-decided",
    "not-proposed",
    "not-approved",
    "target-exists",
    "missing-render",
    "target-escapes-bundle",
    "malformed-proposal",
    "unreadable-target",
]

_EMPTY_FM: Mapping[str, Any] = MappingProxyType({})

#: The provenance keys the capability contributes and a caller may not supply.
#: Exposed rather than private: a caller building a `PageRender` needs to know
#: what it may not put in one, and `plan_create` names this set in the
#: `ValueError` it raises.
OWNED_PROVENANCE_KEYS: tuple[str, ...] = ("generated", "sources", "verified")


@dataclass(frozen=True, slots=True)
class Refusal:
    """One reason this plan will not be applied. Any refusal invalidates all of it."""

    path: str  # bundle-relative posix: the member the refusal is about
    kind: RefusalKind
    detail: str


@dataclass(frozen=True, slots=True)
class Proposal:
    """One proposal document, read.

    `target` is **identity** -- two proposals naming the same target are the
    same proposal and merge. `member` is placement, and is where a refusal
    points a human.

    `page_status` is `None` exactly when the raw value is outside
    `PAGE_STATUSES`; `raw_page_status` keeps what was on disk so a refusal can
    name it. `malformed` carries the reason a proposal could not be read
    cleanly, and is `None` for every usable one -- the flag `list_proposals`
    reports rather than dropping the document, because a proposal that is
    invisible is one nobody fixes.
    """

    member: str  # bundle-relative posix
    concept_id: str
    target: str  # normalized, bundle-relative posix; "" when unusable
    title: str
    description: str
    page_status: PageStatus | None
    raw_page_status: str
    sources: tuple[Mapping[str, Any], ...]
    verified: tuple[Mapping[str, Any], ...]
    malformed: str | None = None


@dataclass(frozen=True, slots=True)
class PageRender:
    """What the **caller** computed for a page this capability will write.

    Named `PageRender` rather than `Render` because `okf_ext.generators.Render`
    already exists and means something else -- fragments merged into a document
    that is already there. This one is a whole page.

    **The capability owns the merge, never the render.** `type`, the body, and
    any lane-specific frontmatter are the caller's; `generated`, `sources` and
    `verified` are the capability's and supplying them raises -- see
    `OWNED_PROVENANCE_KEYS`.
    """

    type: str
    body: str
    frontmatter: Mapping[str, Any] = _EMPTY_FM


@dataclass(frozen=True, slots=True)
class Write:
    """One member this plan writes, in one of the two modes.

    `mode="create"` carries `text`: the whole file, exactly as it will land.
    `mode="update"` carries `frontmatter` (keys set wholesale -- there is no
    delete, because nothing this capability does removes a key), an optional
    `body` replacing the whole body, and `digest`, the plan-time fingerprint
    `apply` refuses a drifted body against.

    A frontmatter-only update carries `body=None` and `digest=None`
    deliberately: a key name does not move, so there is nothing positional to
    go stale -- the same call `okf_ext.generators.apply` documents.
    """

    member: str  # bundle-relative posix
    mode: Mode
    text: str = field(default="", repr=False)
    frontmatter: Mapping[str, Any] = _EMPTY_FM
    body: str | None = field(default=None, repr=False)
    digest: str | None = None


@dataclass(frozen=True, slots=True)
class ProposalPlan:
    """A preview of filing or merging one proposal.

    A value rather than a `dry_run=True` flag, for the reason `MovePlan` and
    `RegenerationPlan` both give. **Idempotence surfaces as an empty plan**: a
    merge whose incoming sources are all already on the document contributes no
    `Write` at all, and `is_empty` is the signal.
    """

    root: Path
    target: str
    proposal: str  # the proposal member this plan writes
    writes: tuple[Write, ...]
    refusals: tuple[Refusal, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def is_empty(self) -> bool:
        return not self.writes


@dataclass(frozen=True, slots=True)
class DecisionPlan:
    """A preview of approving or rejecting one proposal.

    Two edits, always on one document: the `page_status` flip and the
    `verified` append naming the deciding actor. After this transition no code
    path re-renders the body -- the ownership flip is structural, not a flag.
    """

    root: Path
    proposal: str
    decision: Decision
    writes: tuple[Write, ...]
    refusals: tuple[Refusal, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def is_empty(self) -> bool:
        return not self.writes


@dataclass(frozen=True, slots=True)
class PagePlan:
    """A preview of writing a page, through either of the two doors.

    `proposal` is the ledger member whose `page_status` this plan also flips,
    and is `None` for the direct-request door -- `plan_create` writes a page
    with no ledger behind it.

    Page write and ledger flip are **one plan** for a promotion: they cannot be
    applied separately and drift. The page write is ordered first, because
    `write_all` commits in the order given, so a partial failure leaves a
    proposal still `approved` rather than a ledger claiming `created` for a
    page that never landed.
    """

    root: Path
    target: str
    mode: Mode
    proposal: str | None
    writes: tuple[Write, ...]
    refusals: tuple[Refusal, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def is_empty(self) -> bool:
        return not self.writes


#: Every plan `apply` accepts. A union rather than a base class: the three
#: carry different fields and share only what `apply` reads (`root`, `writes`,
#: `refusals`), so a shared ancestor would be inheritance for three attributes.
Plan = ProposalPlan | DecisionPlan | PagePlan


def source_ids(sources: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Every `id` in *sources*, in order, blanks included as `""`.

    Shared by the merge in `plan.py` and by the body render, so "which
    footnote key belongs to which source" is answered once.
    """
    return tuple(str(source.get("id") or "").strip() for source in sources)


#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this.
__all__ = [
    "OWNED_PROVENANCE_KEYS",
    "PAGE_STATUSES",
    "PROPOSAL_TYPE",
    "Decision",
    "DecisionPlan",
    "Mode",
    "PagePlan",
    "PageRender",
    "PageStatus",
    "Plan",
    "Proposal",
    "ProposalPlan",
    "Refusal",
    "RefusalKind",
    "Write",
    "source_ids",
]

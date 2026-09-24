"""Archiving a wiki page: one filtered move batch, then an index reconcile.

Mirrors `work_tracker_okf.archive`'s shape (`ArchivePlan`, `ArchiveResult`,
`Skipped`, `plan_archive`, `apply_archive`) over `okf_ext.moves`, but a wiki
bundle has no single lane the way `work/` is for work items -- a page is
addressed by **path-qualified token** (`"<lane>/<slug>"`, e.g.
`"adrs/2026-08-12-foo"`), against a lane vocabulary the caller resolves from
the loaded schema set via `wiki_lanes`.

**Targeted mode is unconditional** (D-019): a named token that resolves to a
page moves regardless of any status field. `entities/` (code-wiki-okf/scan
owns their lifecycle exclusively) and `work/` (`work_tracker_okf.archive`'s
own job) are not wiki lanes at all, so a token naming either is
`unknown-member`, the same as a token naming nothing.

**Sweep mode keeps today's actual eligible set: proposals only.**
Concepts/sources/adrs carry no status signal to sweep against; a proposal's
`page_status` is one, via `okf_ext.proposals.list_proposals`.

**Two lenses, like `work_tracker_okf.items.IGNORE`/`ARCHIVE_IGNORE`.**
`okf_ext.moves` builds its mapping from `bundle.concepts` and `bundle.assets`
and never from `bundle.ignored`, so a plan computed under the narrow lens
would leave a `sources/` page's `references/` companions behind. `ARCHIVE_IGNORE`
is the wide lens `plan_archive`'s caller must load through; `IGNORE` is the
narrow lens `apply_archive` reconciles through, so `update_index` never wants
an entry for `sources/references/` itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Literal

from okf_ext import moves
from okf_ext.moves import MovePlan, MoveResult, Refusal, stranded_summary
from okf_ext.proposals import list_proposals
from okf_ext.schemas import DEFAULT_IGNORE as _SCHEMA_IGNORE
from okf_ext.schemas import SchemaSet
from okf_ext.shape import DEFAULT_IGNORE as _SECTIONS_IGNORE
from okf_io import Bundle, Describe, EntryTarget, IndexUpdate, load_bundle, update_index

from doc_wiki_okf.diataxis.pages import directory_for
from doc_wiki_okf.sources import REFERENCES_DIRECTORY

#: Why a named token did not move. A closed vocabulary. Narrower than
#: `work_tracker_okf.archive.SkipReason`: targeted mode has no eligibility
#: gate, so there is no `not-terminal` here.
SkipReason = Literal["unknown-member", "already-archived"]

#: The six wiki types whose lane this package ships a schema for. Type names
#: are legitimately this package's vocabulary; directories are not -- those
#: come off the schema set, so `wiki_lanes` and `x-okf-directory` cannot drift
#: apart. `Proposal` is absent deliberately: no shipped schema declares it
#: (`resources.SEED_RELATIVE_PATHS`), so its lane is the one fixed convention
#: below.
WIKI_LANE_TYPES: tuple[str, ...] = (
    "Tutorial",
    "HowTo",
    "Reference",
    "Explanation",
    "Adr",
    "Source",
)

#: The proposal lane, trailing slash, matching `proposals.lanes.ADR_DIRECTORY`'s
#: convention. Named once here because no schema declares it.
PROPOSALS_DIRECTORY = "proposals/"


def wiki_lanes(schema_set: SchemaSet) -> tuple[str, ...]:
    """Every wiki lane a token may name, bundle-relative posix, no trailing
    slash, longest first.

    Longest first so a prefix lookup is unambiguous when one lane's directory
    prefixes another's (`docs` versus `docs/explanations`) -- the same ordering
    `LaneSet.lane_for` computes for itself.

    Reads a `SchemaSet` it is handed and touches no file, so this module stays
    as pure as it was when the vocabulary was a constant; the I/O stays with
    the caller that was already doing it.

    Raises `KeyError` for a wiki type the set does not carry -- caller
    configuration, the same contract `directory_for` and `lane_set` state.
    """
    declared = [directory_for(schema_set, type_name).rstrip("/") for type_name in WIKI_LANE_TYPES]
    declared.append(PROPOSALS_DIRECTORY.rstrip("/"))
    return tuple(sorted(dict.fromkeys(declared), key=len, reverse=True))


#: The `ignore=` recipe `apply_archive` reconciles through. `*/references/*`
#: crosses `/` (okf-io's `*` does), so this hides `sources/references/`
#: (and, harmlessly, a `references/` anywhere else) the same way
#: `work_tracker_okf.items.IGNORE` hides `*/references/*` bundle-wide.
#:
#: `*/.DS_Store` is here and deliberately **not** in `ARCHIVE_IGNORE`: the two
#: lenses want opposite answers. Reading, it is not content; moving, it has to
#: stay visible or `okf_ext.moves` leaves it behind and the source directory
#: never empties for `apply` to prune.
IGNORE: tuple[str, ...] = (
    "sources/references/*",
    "*/sources/references/*",
    "*/.DS_Store",
    *_SCHEMA_IGNORE,
    *_SECTIONS_IGNORE,
)

#: `IGNORE` minus the `references/` patterns. The recipe a caller must load
#: *through* for `plan_archive`/`apply_archive`'s own bundle argument:
#: `okf_ext.moves` never reads `bundle.ignored`, so under `IGNORE` a
#: `sources/` page's `references/` companions are invisible to the planner and
#: the archive is silently half done.
ARCHIVE_IGNORE: tuple[str, ...] = (*_SCHEMA_IGNORE, *_SECTIONS_IGNORE)


@dataclass(frozen=True, slots=True)
class Skipped:
    """One named token that will not move, and why."""

    token: str
    reason: SkipReason
    detail: str


@dataclass(frozen=True, slots=True)
class ArchivePlan:
    """What archiving would move. Speaks the same `ok`/`changed`/`diff()`
    vocabulary `work_tracker_okf.archive.ArchivePlan` does.

    `lanes` is the resolved vocabulary the plan was computed against, carried
    so `apply_archive` reconciles the same lanes the plan filtered -- rather
    than re-deriving them, which would need a schema set it has no reason to
    hold.
    """

    root: Path
    tokens: tuple[str, ...]
    skipped: tuple[Skipped, ...]
    moves: MovePlan
    lanes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.moves.ok

    @property
    def changed(self) -> bool:
        return self.ok and bool(self.tokens)

    def diff(self) -> str:
        """Render the plan. Writes nothing."""
        lines: list[str] = []
        for refusal in self.moves.refusals:
            lines.append(f"! {refusal.path}: {refusal.kind} -- {refusal.detail}")
        for skip in self.skipped:
            lines.append(f"- {skip.token}: skipped ({skip.reason}) -- {skip.detail}")
        for move in self.moves.moves:
            lines.append(f"  {move.source} -> {move.dest}")
        for edit in self.moves.edits:
            lines.append(f"  ~ {edit.member}: {edit.old} -> {edit.new}")
        if self.moves.stranded:
            lines.append(stranded_summary(self.moves.stranded))
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    """What happened."""

    archived: tuple[str, ...]
    skipped: tuple[Skipped, ...]
    refusals: tuple[Refusal, ...]
    move: MoveResult
    indexes: tuple[IndexUpdate, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals and self.move.ok


_EMPTY_MOVE = MoveResult(moved=(), written=(), failed=(), pruned=())


def _lane_of(token: str, lanes: Sequence[str]) -> str | None:
    """The wiki lane *token* names, or `None` if it names none.

    A token is `<lane>/<slug>`: the longest member of *lanes* that prefixes
    *token*, with exactly one path segment left over. A bare word, a token
    deeper than its lane (`"docs/explanations/a/b"`), and an unknown lane
    (`"code-graph/foo"`, `"work/foo"`) all name no lane.

    The "one segment left over" half is the old "exactly one slash" rule,
    preserved rather than relaxed: a page sits directly in its lane.

    *lanes* is sorted here rather than assumed sorted. `wiki_lanes` already
    returns longest-first, but a lookup whose correctness depends on the
    caller's ordering is a defect waiting for the one caller that builds the
    tuple by hand.
    """
    for lane in sorted(lanes, key=len, reverse=True):
        if not token.startswith(f"{lane}/"):
            continue
        rest = token[len(lane) + 1 :]
        return lane if rest and "/" not in rest else None
    return None


def _page(token: str) -> str:
    return f"{token}.md"


def _archived_page(token: str, lane: str) -> str:
    slug = token[len(lane) + 1 :]
    return f"{lane}/_archive/{slug}.md"


def _select(bundle: Bundle, tokens: Sequence[str], lanes: Sequence[str]) -> tuple[tuple[str, ...], tuple[Skipped, ...]]:
    """Targeted mode: unconditional per D-019. The only checks are lane
    membership and not being archived already."""
    chosen: list[str] = []
    skipped: list[Skipped] = []
    for token in dict.fromkeys(tokens):  # one record per named token, order preserved
        lane = _lane_of(token, lanes)
        if lane is None:
            skipped.append(Skipped(token, "unknown-member", f"{token!r} names no known wiki lane"))
            continue
        raw_page = bundle.member_id(_page(token))
        if raw_page is not None:
            chosen.append(raw_page[:-3])
        # has_member, not member_id: this check is message-only (the token
        # feeds a human-readable Skipped reason, not a further lookup or
        # write), and the boolean answer is NFC-insensitive either way.
        elif bundle.has_member(_archived_page(token, lane)):
            skipped.append(Skipped(token, "already-archived", f"{token} already sits under {lane}/_archive/"))
        else:
            skipped.append(Skipped(token, "unknown-member", f"no page for {token} under {lane}/"))
    return tuple(sorted(chosen)), tuple(skipped)


def _sweep(bundle: Bundle, lanes: Sequence[str]) -> tuple[tuple[str, ...], tuple[Skipped, ...]]:
    """Every eligible proposal, or none. A sweep's non-candidates were never
    candidates (matching `work_tracker_okf.archive._select`'s own C4-G), so
    this carries no `Skipped`.

    Eligible means a *coerced* `page_status` other than `"proposed"` -- a
    malformed proposal (`page_status is None`) has no status signal to sweep
    against and is left alone, matching `_select`'s own treatment of an
    uncoercible proposal page status -- **and** a member that resolves to a
    lane. `list_proposals` enumerates by `type:`, not by path, so a `Proposal`
    sitting outside every lane can come back; it has no lane to build an
    `_archive/` path under, and silently dropping it is what "never a
    candidate" means.
    """
    chosen = sorted(
        proposal.member[:-3]  # strip ".md": member is already bundle-relative posix
        for proposal in list_proposals(bundle)
        if proposal.page_status is not None
        and proposal.page_status != "proposed"
        and _lane_of(proposal.member[:-3], lanes) is not None
    )
    return tuple(chosen), ()


def _members_under(bundle: Bundle, prefix: str) -> tuple[str, ...]:
    """Every member (concept or asset) whose path starts with *prefix*."""
    found = {f"{cid}.md" for cid in bundle.concepts if f"{cid}.md".startswith(prefix)}
    found.update(asset for asset in bundle.assets if asset.startswith(prefix))
    return tuple(sorted(found))


def _reference_companions(bundle: Bundle, source_slug: str) -> dict[str, str]:
    """`sources/references/<source_slug>.*` -> its `_archive/` twin, if present.

    The one lane-specific special case: a `sources/` page's material travels
    with it, matched by filename stem. Already-archived companions
    (`sources/references/_archive/...`) are excluded by the nested-segment
    check below.
    """
    prefix = f"sources/{REFERENCES_DIRECTORY}/"
    mapping: dict[str, str] = {}
    for member in _members_under(bundle, prefix):
        name = member[len(prefix) :]
        if "/" in name:
            continue
        if PurePosixPath(name).stem == source_slug:
            mapping[member] = f"{prefix}_archive/{name}"
    return mapping


def _mapping(tokens: Sequence[str], bundle: Bundle, lanes: Sequence[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for token in tokens:
        lane = _lane_of(token, lanes)
        if lane is None:  # pragma: no cover -- `_select`/`_sweep` only yield resolvable tokens
            continue
        mapping[_page(token)] = _archived_page(token, lane)
        if lane == "sources":
            mapping.update(_reference_companions(bundle, token[len(lane) + 1 :]))
    return mapping


def plan_archive(bundle: Bundle, tokens: Sequence[str] | None = None, *, lanes: Sequence[str]) -> ArchivePlan:
    """Plan the archive of *tokens*, or of every eligible proposal when `None`.

    *bundle* must be loaded through `ARCHIVE_IGNORE`; through `IGNORE` a
    `sources/` page's `references/` companions are invisible to `moves` and
    the archive is silently half done.

    *lanes* is the wiki lane vocabulary, from `wiki_lanes(schema_set)`. It is
    required and keyword-only: a default would be a second, drifting copy of
    exactly the constant this argument replaced.

    Writes nothing.
    """
    resolved = tuple(lanes)
    chosen, skipped = _sweep(bundle, resolved) if tokens is None else _select(bundle, tokens, resolved)
    plan = moves.plan_move_many(bundle, _mapping(chosen, bundle, resolved))
    # Drop every touched lane's own index edits, active and `_archive/` form,
    # so `update_index` owns index content end to end -- the same reason
    # work_tracker_okf.archive.plan_archive filters `_LANE_INDEXES`.
    touched = {lane for token in chosen if (lane := _lane_of(token, resolved)) is not None}
    lane_indexes = {f"{lane}/index.md" for lane in touched} | {f"{lane}/_archive/index.md" for lane in touched}
    filtered = replace(plan, edits=tuple(edit for edit in plan.edits if edit.member not in lane_indexes))
    return ArchivePlan(root=bundle.root, tokens=chosen, skipped=skipped, moves=filtered, lanes=resolved)


def _harvest(bundle: Bundle, lanes: Sequence[str]) -> Mapping[str, str]:
    """The authored one-liner for every drifted entry across *lanes*' indexes.

    Dry-run (no `dry_run=` passed to `update_index`), so this reads and
    writes nothing. A lane with no index yet contributes nothing --
    `update_index` raises for a directory the bundle does not contain.
    """
    present = [lane for lane in lanes if lane in bundle.indexes]
    if not present:
        return {}
    return {drift.target: drift.text for update in update_index(bundle, directories=present) for drift in update.drift}


def _describe_for(harvested: Mapping[str, str], lanes: Sequence[str]) -> Describe:
    def describe(target: EntryTarget) -> str | None:
        for lane in sorted(lanes, key=len, reverse=True):
            prefix = f"{lane}/_archive/"
            if target.path.startswith(prefix):
                active = f"{lane}/{target.path[len(prefix) :]}"
                carried = harvested.get(active)
                if carried is not None:
                    return carried
                break
        if target.document is not None:
            return (target.document.fm.description or "").strip() or None
        return None

    return describe


def _present_directories(bundle: Bundle, directories: Sequence[str]) -> list[str]:
    """*directories* filtered to the ones *bundle* actually contains.

    `update_index` raises for a directory the bundle does not contain. A
    lane's `_archive/` form always qualifies right after a successful move --
    the moved page just landed there -- but archiving a lane's *last*
    remaining page can empty its active form entirely when that lane never
    had an `index.md` of its own to keep it present.
    """
    present: list[str] = []
    for directory in directories:
        prefix = f"{directory}/"
        if (
            directory in bundle.indexes
            or any(f"{cid}.md".startswith(prefix) for cid in bundle.concepts)
            or any(asset.startswith(prefix) for asset in bundle.assets)
        ):
            present.append(directory)
    return present


def apply_archive(bundle: Bundle, plan: ArchivePlan) -> ArchiveResult:
    """Apply *plan*: move, then reconcile every touched lane's index (active
    and `_archive/` form).

    No `dry_run`: `ArchivePlan` is the preview, and honestly reporting
    `ArchiveResult.indexes` needs the move to have already happened.

    A non-`ok` plan **returns**, it does not raise: a refusal is content.
    """
    if not plan.ok:
        return ArchiveResult(
            archived=(), skipped=plan.skipped, refusals=plan.moves.refusals, move=_EMPTY_MOVE, indexes=()
        )
    if not plan.tokens:
        return ArchiveResult(archived=(), skipped=plan.skipped, refusals=(), move=_EMPTY_MOVE, indexes=())

    lanes = sorted({lane for token in plan.tokens if (lane := _lane_of(token, plan.lanes)) is not None})
    harvested = _harvest(bundle, lanes)
    result = moves.apply(bundle, plan.moves)

    landed = {source for source, _ in result.moved}
    archived = tuple(token for token in plan.tokens if _page(token) in landed)

    indexes: tuple[IndexUpdate, ...] = ()
    if archived:
        reloaded = load_bundle(plan.root, ignore=IGNORE)
        touched = sorted({lane for token in archived if (lane := _lane_of(token, plan.lanes)) is not None})
        candidates = [d for lane in touched for d in (lane, f"{lane}/_archive")]
        indexes = update_index(
            reloaded,
            directories=_present_directories(reloaded, candidates),
            describe=_describe_for(harvested, plan.lanes),
            create_missing=True,
            dry_run=False,
        )

    return ArchiveResult(archived=archived, skipped=plan.skipped, refusals=(), move=result, indexes=indexes)


__all__ = [
    "ARCHIVE_IGNORE",
    "IGNORE",
    "PROPOSALS_DIRECTORY",
    "WIKI_LANE_TYPES",
    "ArchivePlan",
    "ArchiveResult",
    "SkipReason",
    "Skipped",
    "apply_archive",
    "plan_archive",
    "wiki_lanes",
]

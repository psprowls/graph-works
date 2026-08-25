"""Archiving a wiki page: one filtered move batch, then an index reconcile.

Mirrors `work_tracker_okf.archive`'s shape (`ArchivePlan`, `ArchiveResult`,
`Skipped`, `plan_archive`, `apply_archive`) over `okf_ext.moves`, but a wiki
bundle has no single lane the way `work/` is for work items -- a page is
addressed by **path-qualified token** (`"<lane>/<slug>"`, e.g.
`"adrs/2026-08-12-foo"`) rather than a bare slug.

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
from okf_ext.shape import DEFAULT_IGNORE as _SECTIONS_IGNORE
from okf_io import Bundle, Describe, EntryTarget, IndexUpdate, load_bundle, update_index

from doc_wiki_okf.sources import REFERENCES_DIRECTORY

#: Why a named token did not move. A closed vocabulary. Narrower than
#: `work_tracker_okf.archive.SkipReason`: targeted mode has no eligibility
#: gate, so there is no `not-terminal` here.
SkipReason = Literal["unknown-member", "already-archived"]

#: The seven wiki lanes a token may name, bundle-relative posix, no trailing
#: slash. The four Diátaxis directories are the schemas' own
#: `x-okf-directory` values (`how-tos/`, `references/`, `explanations/` are
#: plural/derived forms, not the Diátaxis *names* `doc_wiki_okf.proposals.lanes`
#: uses); `adrs`, `sources` and `proposals` are the fixed conventions used
#: throughout this package. Hardcoded, matching how `ADR_DIRECTORY` and
#: `REFERENCES_DIRECTORY` are also fixed conventions rather than schema reads:
#: a pure string function should not do file I/O.
WIKI_LANES: tuple[str, ...] = (
    "tutorials",
    "how-tos",
    "references",
    "explanations",
    "adrs",
    "sources",
    "proposals",
)

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
    vocabulary `work_tracker_okf.archive.ArchivePlan` does."""

    root: Path
    tokens: tuple[str, ...]
    skipped: tuple[Skipped, ...]
    moves: MovePlan

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


def _lane_of(token: str) -> str | None:
    """The wiki lane *token* names, or `None` if it names none.

    A token is `<lane>/<slug>` -- exactly one slash, first segment a member
    of `WIKI_LANES`. A bare word, a deeper path, or an unknown first segment
    (`"entities/foo"`, `"work/foo"`) all name no lane.
    """
    lane, sep, rest = token.partition("/")
    if not sep or not rest or "/" in rest:
        return None
    return lane if lane in WIKI_LANES else None


def _page(token: str) -> str:
    return f"{token}.md"


def _archived_page(token: str, lane: str) -> str:
    slug = token[len(lane) + 1 :]
    return f"{lane}/_archive/{slug}.md"


def _select(bundle: Bundle, tokens: Sequence[str]) -> tuple[tuple[str, ...], tuple[Skipped, ...]]:
    """Targeted mode: unconditional per D-019. The only checks are lane
    membership and not being archived already."""
    chosen: list[str] = []
    skipped: list[Skipped] = []
    for token in dict.fromkeys(tokens):  # one record per named token, order preserved
        lane = _lane_of(token)
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


def _sweep(bundle: Bundle) -> tuple[tuple[str, ...], tuple[Skipped, ...]]:
    """Every eligible proposal, or none. A sweep's non-candidates were never
    candidates (matching `work_tracker_okf.archive._select`'s own C4-G), so
    this carries no `Skipped`.

    Eligible means a *coerced* `page_status` other than `"proposed"` -- a
    malformed proposal (`page_status is None`) has no status signal to sweep
    against and is left alone, matching `_select`'s own treatment of an
    uncoercible proposal page status.
    """
    chosen = sorted(
        proposal.member[:-3]  # strip ".md": member is already bundle-relative posix
        for proposal in list_proposals(bundle)
        if proposal.page_status is not None and proposal.page_status != "proposed"
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


def _mapping(tokens: Sequence[str], bundle: Bundle) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for token in tokens:
        lane, slug = token.split("/", 1)
        mapping[_page(token)] = _archived_page(token, lane)
        if lane == "sources":
            mapping.update(_reference_companions(bundle, slug))
    return mapping


def plan_archive(bundle: Bundle, tokens: Sequence[str] | None = None) -> ArchivePlan:
    """Plan the archive of *tokens*, or of every eligible proposal when `None`.

    *bundle* must be loaded through `ARCHIVE_IGNORE`; through `IGNORE` a
    `sources/` page's `references/` companions are invisible to `moves` and
    the archive is silently half done.

    Writes nothing.
    """
    chosen, skipped = _sweep(bundle) if tokens is None else _select(bundle, tokens)
    plan = moves.plan_move_many(bundle, _mapping(chosen, bundle))
    # Drop every touched lane's own index edits, active and `_archive/` form,
    # so `update_index` owns index content end to end -- the same reason
    # work_tracker_okf.archive.plan_archive filters `_LANE_INDEXES`.
    lanes = {token.split("/", 1)[0] for token in chosen}
    lane_indexes = {f"{lane}/index.md" for lane in lanes} | {f"{lane}/_archive/index.md" for lane in lanes}
    filtered = replace(plan, edits=tuple(edit for edit in plan.edits if edit.member not in lane_indexes))
    return ArchivePlan(root=bundle.root, tokens=chosen, skipped=skipped, moves=filtered)


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


def _describe_for(harvested: Mapping[str, str]) -> Describe:
    def describe(target: EntryTarget) -> str | None:
        for lane in WIKI_LANES:
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

    lanes = sorted({token.split("/", 1)[0] for token in plan.tokens})
    harvested = _harvest(bundle, lanes)
    result = moves.apply(bundle, plan.moves)

    landed = {source for source, _ in result.moved}
    archived = tuple(token for token in plan.tokens if _page(token) in landed)

    indexes: tuple[IndexUpdate, ...] = ()
    if archived:
        reloaded = load_bundle(plan.root, ignore=IGNORE)
        touched = sorted({token.split("/", 1)[0] for token in archived})
        candidates = [d for lane in touched for d in (lane, f"{lane}/_archive")]
        indexes = update_index(
            reloaded,
            directories=_present_directories(reloaded, candidates),
            describe=_describe_for(harvested),
            create_missing=True,
            dry_run=False,
        )

    return ArchiveResult(archived=archived, skipped=plan.skipped, refusals=(), move=result, indexes=indexes)


__all__ = [
    "ARCHIVE_IGNORE",
    "IGNORE",
    "WIKI_LANES",
    "ArchivePlan",
    "ArchiveResult",
    "SkipReason",
    "Skipped",
    "apply_archive",
    "plan_archive",
]

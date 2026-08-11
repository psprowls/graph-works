"""Archiving: one filtered move batch, then an index reconcile.

Archiving relocates an already-terminal item and repairs what pointed at it.
**No frontmatter is written** (C4-J) -- which is what makes it a *pure* prefix
move, and this module's whole thesis.

Two named recipes over one lane, for two jobs that provably need different
ones. `plan_archive` takes a bundle loaded through `ARCHIVE_IGNORE`, because
`okf_ext.moves` builds its mapping from concepts, assets, indexes and logs and
never from `bundle.ignored` -- so under `IGNORE` the per-item artifacts are
invisible to the planner and the archive is silently half done. The reconcile
that follows runs against a **reload through `IGNORE`**: under the wider lens
`update_index` would want a `# Subdirectories` entry for every item with a
working directory, and `moves.apply` never updates the in-memory `Bundle`
anyway, so the caller reloads regardless.

Plan and apply stay two calls, not a `dry_run` flag (C4-E). `ArchivePlan` *is*
the dry run, and it is richer than a boolean -- it is inspectable and
filterable, which is what §3.2's index handoff is built on. A `dry_run=True`
here would also have to lie: `ArchiveResult.indexes` cannot be computed without
the move having happened.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from okf_ext import moves
from okf_ext.moves import MovePlan, MoveResult, Refusal
from okf_io import Bundle, Describe, EntryTarget, IndexUpdate, load_bundle, update_index

from work_tracker_okf.items import ARCHIVE_DIR, IGNORE, WORK_DIR, WorkItem, load_items
from work_tracker_okf.paths import item_page
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

#: Why a *named* slug did not move. A closed vocabulary matching
#: `advance.RefusalReason`. `work_io` carried a fourth -- a free-text parse
#: error -- which has no successor: the item view does not refuse a malformed
#: page, it projects one with its uncoercible fields empty, and a page whose
#: `workflow_status` would not coerce simply is not terminal.
SkipReason = Literal["unknown-slug", "not-terminal", "already-archived"]

#: The two index files this package reconciles itself and therefore filters out
#: of the move plan (C4-B). Composed from the lane directories rather than
#: imported from `okf_io.bundle.INDEX_NAME`, which is not on okf-io's front
#: door -- the same call `paths.py` makes about `WORK_DIR`.
_LANE_INDEXES: frozenset[str] = frozenset({f"{WORK_DIR}/index.md", f"{ARCHIVE_DIR}/index.md"})


@dataclass(frozen=True, slots=True)
class Skipped:
    """One named slug that will not move, and why."""

    slug: str
    reason: SkipReason
    detail: str


@dataclass(frozen=True, slots=True)
class ArchivePlan:
    """What archiving would move. Speaks the writer vocabulary `FilingPlan`,
    `AdvancePlan` and `IndexUpdate` already use: `changed`, and a `diff()` that
    renders on demand and writes nothing."""

    root: Path
    slugs: tuple[str, ...]
    skipped: tuple[Skipped, ...]
    moves: MovePlan

    @property
    def ok(self) -> bool:
        """False when the move plan carries any refusal. All-or-nothing by
        construction, which is what makes a sweep fail closed."""
        return self.moves.ok

    @property
    def changed(self) -> bool:
        return self.ok and bool(self.slugs)

    def diff(self) -> str:
        """Render the plan. Writes nothing."""
        lines: list[str] = []
        for refusal in self.moves.refusals:
            lines.append(f"! {refusal.path}: {refusal.kind} -- {refusal.detail}")
        for skip in self.skipped:
            lines.append(f"- {skip.slug}: skipped ({skip.reason}) -- {skip.detail}")
        for move in self.moves.moves:
            lines.append(f"  {move.source} -> {move.dest}")
        for edit in self.moves.edits:
            lines.append(f"  ~ {edit.member}: {edit.old} -> {edit.new}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    """What happened. `pruned` holds **this package's** working-directory
    removals; `moves`' own `references/` prune stays on `move.pruned` (C4-I)."""

    archived: tuple[str, ...]
    skipped: tuple[Skipped, ...]
    refusals: tuple[Refusal, ...]
    move: MoveResult
    indexes: tuple[IndexUpdate, ...]
    pruned: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals and self.move.ok


_EMPTY_MOVE = MoveResult(moved=(), written=(), failed=(), pruned=())


def _lanes(items: Sequence[WorkItem]) -> tuple[dict[str, WorkItem], frozenset[str]]:
    """The active items by slug, and the set of slugs already under `_archive/`.

    Active is checked first everywhere below, so a slug carrying *both* an
    active page and an archived twin stays eligible -- and earns `moves`' own
    `dest-exists` refusal rather than a skip that would hide it.
    """
    active = {item.slug: item for item in items if not item.archived}
    archived = frozenset(item.slug for item in items if item.archived)
    return active, archived


def _select(items: Sequence[WorkItem], slugs: Sequence[str] | None) -> tuple[tuple[str, ...], tuple[Skipped, ...]]:
    active, archived = _lanes(items)
    if slugs is None:
        # C4-G: a sweep's non-candidates were never candidates, so none of them
        # is a skip. Targeted mode is where a skip carries information, because
        # there the caller named the slug and is owed an answer.
        eligible = sorted(slug for slug, item in active.items() if item.workflow_status in TERMINAL_STATUSES)
        return tuple(eligible), ()

    chosen: list[str] = []
    skipped: list[Skipped] = []
    for slug in dict.fromkeys(slugs):  # one record per named slug, order preserved
        item = active.get(slug)
        if item is None:
            if slug in archived:
                skipped.append(Skipped(slug, "already-archived", f"{slug} already sits under {ARCHIVE_DIR}/"))
            else:
                skipped.append(Skipped(slug, "unknown-slug", f"no item page for {slug} under either lane"))
        elif item.workflow_status not in TERMINAL_STATUSES:
            skipped.append(
                Skipped(
                    slug,
                    "not-terminal",
                    f"workflow_status {item.workflow_status!r} is not one of {sorted(TERMINAL_STATUSES)}",
                )
            )
        else:
            chosen.append(slug)
    return tuple(sorted(chosen)), tuple(skipped)


def _mapping(bundle: Bundle, slugs: Sequence[str]) -> dict[str, str]:
    """The whole sweep's mapping: each item's page plus every member under its
    working directory (C4-H).

    `plan_move_dir` is used as an **enumerator** over the working directory and
    its result folded into the batch, rather than applied as its own plan: a
    reference from one archived item's page to another's is rewritten correctly
    only when both ends are in the same mapping.
    """
    mapping: dict[str, str] = {}
    for slug in slugs:
        mapping[item_page(slug).rel] = item_page(slug, archived=True).rel
        enumerated = moves.plan_move_dir(bundle, f"{WORK_DIR}/{slug}", f"{ARCHIVE_DIR}/{slug}")
        mapping.update({move.source: move.dest for move in enumerated.moves})
    return mapping


def plan_archive(bundle: Bundle, slugs: Sequence[str] | None = None) -> ArchivePlan:
    """Plan the archive of *slugs*, or of every eligible item when `None`.

    *bundle* must be loaded through `ARCHIVE_IGNORE`; through `IGNORE` the
    per-item artifacts are invisible to `moves` and the resulting plan reports
    `ok` while covering only the item page.

    Eligibility is read from the item view and nothing else: `workflow_status in
    TERMINAL_STATUSES` and `not archived`. Writes nothing.
    """
    chosen, skipped = _select(load_items(bundle), slugs)
    plan = moves.plan_move_many(bundle, _mapping(bundle, chosen))
    # C4-B: drop the lane indexes' edits so `update_index` owns index content
    # end to end. `MovePlan.members`, the `digests` lookup and `apply`'s
    # `touched` set all derive from `edits`, so the filtered plan is internally
    # consistent with no further surgery. The cost is a transient dangling
    # reference in `work/index.md` between `apply` and the reconcile, never
    # observable to a reader who loads the bundle before or after.
    filtered = replace(plan, edits=tuple(edit for edit in plan.edits if edit.member not in _LANE_INDEXES))
    return ArchivePlan(root=bundle.root, slugs=chosen, skipped=skipped, moves=filtered)


def _harvest(bundle: Bundle) -> Mapping[str, str]:
    """The authored one-liner for every drifted entry in `work/index.md`.

    Extension point #4 is what carries a human's words across the lane
    boundary (C4-C). Under the default `descriptions="preserve"`, `Drift`
    reports exactly the entries whose authored text differs from the generated
    text -- so this map holds exactly the entries worth carrying; for the rest
    the generated text is identical to what was authored anyway.

    Dry-run by default, so this reads and writes nothing.

    The guard is explicit rather than incidental: `update_index` raises
    `ValueError` for a directory the bundle does not contain, and `Bundle.indexes`
    is already keyed by directory id, so this is the whole check.
    """
    if WORK_DIR not in bundle.indexes:
        return {}
    return {
        drift.target: drift.text for update in update_index(bundle, directories=[WORK_DIR]) for drift in update.drift
    }


def _describe_for(harvested: Mapping[str, str]) -> Describe:
    """Map an archived entry back to its active-lane path and answer with the
    harvested text.

    `describe` is **authoritative** in okf-io, its `None` included -- so this
    reproduces okf-io's own default for every target the map does not name,
    rather than silently stripping descriptions from unrelated new entries.
    """

    def describe(target: EntryTarget) -> str | None:
        if target.path.startswith(f"{ARCHIVE_DIR}/"):
            active = f"{WORK_DIR}/{target.path[len(ARCHIVE_DIR) + 1 :]}"
            carried = harvested.get(active)
            if carried is not None:
                return carried
        if target.document is not None:
            return (target.document.fm.description or "").strip() or None
        return None

    return describe


def apply_archive(bundle: Bundle, plan: ArchivePlan) -> ArchiveResult:
    """Apply *plan*: move, prune, then reconcile both lane indexes.

    No `dry_run` (C4-E): `ArchivePlan` is the preview, and a dry run could not
    honestly report its `IndexUpdate`s -- they cannot be computed without the
    move having happened, because the reconcile reads a bundle reloaded from
    the new paths.

    A non-`ok` plan **returns**, it does not raise (C4-F): nothing on this
    package's content path raises, and a refusal is content --
    `unlocatable-reference` and `reference-definition` are judgments about
    prose someone wrote. `moves.apply` is never reached, so its `ValueError`
    stays unreachable through this API.

    A **no-op archive touches no index**: otherwise a sweep finding nothing
    would still rewrite `work/index.md` to add the `# Subdirectories` block.
    """
    if not plan.ok:
        return ArchiveResult(
            archived=(),
            skipped=plan.skipped,
            refusals=plan.moves.refusals,
            move=_EMPTY_MOVE,
            indexes=(),
            pruned=(),
        )
    if not plan.slugs:
        return ArchiveResult(
            archived=(),
            skipped=plan.skipped,
            refusals=(),
            move=_EMPTY_MOVE,
            indexes=(),
            pruned=(),
        )

    harvested = _harvest(bundle)
    result = moves.apply(bundle, plan.moves)

    landed = {source for source, _ in result.moved}
    archived = tuple(slug for slug in plan.slugs if item_page(slug).rel in landed)
    pruned = _prune_working_directories(plan.root, archived)

    indexes: tuple[IndexUpdate, ...] = ()
    if archived:
        # The reconcile uses the **other** lens: a reload through `IGNORE`, so
        # `update_index` does not want a `# Subdirectories` entry for every
        # item's working directory. `create_missing=True` for these two
        # directories only (C4-D) -- `init` scaffolds neither lane index, and a
        # first archive into a fresh `_archive/` would otherwise write none.
        reloaded = load_bundle(plan.root, ignore=IGNORE)
        indexes = update_index(
            reloaded,
            directories=[WORK_DIR, ARCHIVE_DIR],
            describe=_describe_for(harvested),
            create_missing=True,
            dry_run=False,
        )

    return ArchiveResult(
        archived=archived,
        skipped=plan.skipped,
        refusals=(),
        move=result,
        indexes=indexes,
        pruned=pruned,
    )


def _prune_working_directories(root: Path, slugs: Sequence[str]) -> tuple[str, ...]:
    """Remove each archived item's emptied working directory. Best-effort, no
    failure path -- matching `moves._prune`'s own posture (C4-I).

    `moves._prune` declines proper *ancestors* deliberately: an empty directory
    is invisible to `load_bundle`, so leaving one costs okf-ext nothing, and
    climbing the tree would have it deleting directories no move ever named.
    For this lane the emptied directory the done-when means *is* the working
    directory, so this package removes its own.
    """
    pruned: list[str] = []
    for slug in slugs:
        relative = f"{WORK_DIR}/{slug}"
        target = root / relative
        try:
            if target.is_dir() and not any(target.iterdir()):
                target.rmdir()
                pruned.append(relative)
        except OSError:
            continue
    return tuple(pruned)


__all__ = ["ArchivePlan", "ArchiveResult", "SkipReason", "Skipped", "apply_archive", "plan_archive"]

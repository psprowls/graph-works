"""Top-level entity-lane sync: `sync_entities` (Task 4) + `prune_lane`
(Task 5) once per lane, touched-lane `index.md` reconciliation, and one
`log.md` entry per run. The entity half of the eventual `sync` CLI command
(a later child adds the mirror half onto the same command, not a second
one).

**`dry_run=True` (the default) calls nothing.** Neither `sync_entities` nor
`prune_lane` carries its own `dry_run` -- each commits its writes/deletes the
moment it is called (Tasks 4 and 5, already reviewed and approved elsewhere;
widening that contract is out of scope here). The only way this function's
own `dry_run` can honestly guarantee "nothing touches disk" is to skip the
whole pipeline rather than call either and hope. `dry_run=False` is what
makes anything happen at all, including the entity sync step itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from code_graph_io import GraphReader
from okf_ext.shape import load_sections
from okf_io import Bundle, append_log_entry, load_bundle, update_index

from code_wiki_okf.config import Config
from code_wiki_okf.entities.delete import prune_lane
from code_wiki_okf.entities.sync import sync_entities

#: Bundle-relative prefixes `prune_lane` scopes deletion to, one per entity
#: kind. Trailing slash matches `prune_lane`'s own `directory=` contract
#: (`entities/delete.py`) -- a plain `"repositories/"` prefix only ever
#: matches a page directly under it, never a repo's own
#: `repositories/<name>/` mirror subtree (a later child's business).
#:
#: Public (no leading underscore) so `code_wiki_okf.sync.snapshot` can derive
#: its own entity-lane prefix check from this single source rather than
#: hand-duplicating the tuple -- a future lane added here must not be able to
#: silently fall out of sync with what `.orphaned` considers "existing".
ENTITY_LANES: tuple[str, ...] = (
    "packages/",
    "apps/",
    "test-suites/",
    "dependencies/",
    "agent-plugins/",
    "repositories/",
)


@dataclass(frozen=True, slots=True)
class SyncSummary:
    """What one `sync()` run did, across every entity lane.

    `written` and `skipped` are `sync_entities`' own `EntitySync` fields,
    passed through unchanged. `deleted` and `declined` are `prune_lane`'s
    results pooled across every lane in `ENTITY_LANES`.
    """

    written: tuple[str, ...] = field(default_factory=tuple)
    skipped: tuple[str, ...] = field(default_factory=tuple)
    deleted: tuple[str, ...] = field(default_factory=tuple)
    declined: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (concept_id, reason)


def _summary_text(summary: SyncSummary) -> str:
    """One `log.md` bullet naming this run's counts.

    `declined` is broken down by reason rather than assumed to be
    `"prose-edited"`: `prune_lane` (`entities/delete.py`) also declines with
    `"no-declaration-for-type"`, and a summary that only ever said "prose
    edited" would misreport that case.
    """
    parts = [f"entity sync: {len(summary.written)} page(s) created/updated, {len(summary.deleted)} deleted"]
    if summary.skipped:
        parts[0] += f", {len(summary.skipped)} skipped"
    if summary.declined:
        reasons: dict[str, int] = {}
        for _concept_id, reason in summary.declined:
            reasons[reason] = reasons.get(reason, 0) + 1
        breakdown = ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
        parts.append(f"{len(summary.declined)} deletion(s) declined ({breakdown})")
    return "; ".join(parts)


def sync(
    bundle: Bundle,
    config: Config,
    reader: GraphReader,
    *,
    today: date,
    at: datetime,
    dry_run: bool = True,
) -> SyncSummary:
    """Run the full entity-lane pipeline against *bundle*'s root.

    `sync_entities` runs once. Then `prune_lane` runs once per lane in
    `ENTITY_LANES`, each lane's deletion candidates compared against
    `EntitySync.current_resources` -- every resource this run's graph walk
    still names, whether or not that resource's page needed a write --
    **never** `resource_index(bundle)`. That index only ever reflects what
    is already on disk, which trivially includes the very stale pages
    deletion exists to find; using it as "should exist" would make deletion
    a permanent no-op.

    Every lane touched by a create/update (`written`) or a delete/decline
    gets its `index.md` reconciled with `create_missing=True`. Exactly one
    `log.md` entry is appended, naming this run's counts -- even a run that
    changed nothing gets one, so the log stays a complete record of every
    sync, not just the ones that did something.
    """
    if dry_run:
        return SyncSummary()

    entity_result = sync_entities(bundle, config, reader, today=today, at=at)

    # `sync_entities` already committed its writes; reload once so deletion
    # sees the pages it just created or regenerated.
    current = load_bundle(bundle.root)
    section_set = load_sections(current.root / "_sections")
    should_exist = set(entity_result.current_resources)

    deleted: list[str] = []
    declined: list[tuple[str, str]] = []
    touched_lanes: set[str] = set()
    for lane in ENTITY_LANES:
        result = prune_lane(
            current, section_set, directory=lane, should_exist=should_exist, exact_depth=(lane == "repositories/")
        )
        deleted.extend(result.deleted)
        declined.extend(result.declined)
        if result.deleted or result.declined:
            touched_lanes.add(lane.rstrip("/"))
    touched_lanes.update(concept_id.split("/", 1)[0] for concept_id in entity_result.written)

    # Reload again: `current` was walked before deletion, so its own
    # `.concepts` would still list every page `prune_lane` just removed from
    # disk, and `update_index` decides an entry is dead by asking
    # `bundle.has_member` -- which would still say "yes" against that now
    # stale snapshot.
    reconciled = load_bundle(current.root)
    if touched_lanes:
        update_index(reconciled, directories=sorted(touched_lanes), create_missing=True, dry_run=False)

    log_document = reconciled.logs.get("")
    if log_document is None:
        raise ValueError(
            f"{bundle.root}: no root log.md -- every code-wiki-okf bundle is created with one by init_bundle()"
        )

    summary = SyncSummary(
        written=entity_result.written,
        skipped=entity_result.skipped,
        deleted=tuple(deleted),
        declined=tuple(declined),
    )
    append_log_entry(log_document, _summary_text(summary), today=today, dry_run=False)
    return summary


__all__ = ["ENTITY_LANES", "SyncSummary", "sync"]

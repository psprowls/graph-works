"""The six work display reads as they were before the read session: a full load per call.

Kept as the equivalence oracle; do not modernize.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from doc_wiki_okf.sources import SOURCE_TYPE, normalize_origin
from graph_works_core.work.commands import (
    IngestQueueReport,
    ItemRead,
    ItemSource,
    OpenDecision,
    PendingIngest,
    QueueEntry,
    StatusReport,
    _definition_of,
    _load_config,
    _owned_references,
    _plan_route,
    _refused_item,
    _resolve_dispatch_with,
)
from graph_works_core.workspace.bundle import load_work_bundle, load_workspace_bundle
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_io import Bundle
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.hierarchy import decision_owner
from work_tracker_okf.items import IGNORE, WorkItem, item_index, load_items, unreadable_detail
from work_tracker_okf.projection import rollup, select_resume
from work_tracker_okf.vocabulary import SPEC_SOURCE_ID, TERMINAL_STATUSES


def oracle_status(layout: WorkspaceLayout) -> StatusReport:
    """Count the active items and name the one worth resuming. Never writes."""
    items = load_items(load_work_bundle(layout))
    return StatusReport(rollup=rollup(items), resume=select_resume(items))


def oracle_work_list(layout: WorkspaceLayout) -> tuple[WorkItem, ...]:
    """Every active work item, sorted by canonical path. Never writes.

    Archived items are left out, the same population `rollup` counts, so a
    board built from this agrees with `gw work status`.
    """
    items = load_items(load_work_bundle(layout))
    return tuple(sorted((item for item in items if not item.archived), key=lambda item: item.path))


def oracle_item_read(layout: WorkspaceLayout, path: str) -> ItemRead:
    """Read *path*'s work item and list its owned `references/`. Never writes."""
    bundle = load_work_bundle(layout)
    if path not in item_index(load_items(bundle)):
        detail = unreadable_detail(bundle, path)
        return _refused_item(path, "unreadable" if detail is not None else "unknown-item", detail)
    document = bundle.concepts[path]
    error = document.parse_error
    return ItemRead(
        path=path,
        frontmatter=MappingProxyType(document.fm_data(dates="iso")),
        body=document.body,
        sources=tuple(ItemSource(source.id, source.resource, source.title) for source in document.fm.sources),
        references=_owned_references(layout.bundle_dir, path),
        parse_error=None if error is None else f"{error.kind}: {error.message}",
        coercion_failures=tuple(sorted(document.fm.coercion_failures)),
        refusal=None,
        detail=None,
    )


def _ingested_origins(bundle: Bundle) -> frozenset[str]:
    """Every normalized `origin` some `Source` page in *bundle* already carries.

    A `Source` page with **no** `origin` contributes nothing. It is not evidence
    of anything -- only 10 of the live vault's Source pages populate the field
    -- so its item stays queued. The queue therefore over-reports rather than
    under-reports, which is the correct direction of error: a re-ingest is
    idempotent (it appends a `## Re-ingest <date>` section) and costs a human
    one "already done, skip", while an under-report silently loses a design
    spec.
    """
    origins: set[str] = set()
    for document in bundle.concepts.values():
        if document.fm.type != SOURCE_TYPE:
            continue
        stored = document.fm.extra.get("origin")
        if isinstance(stored, str) and stored:
            origins.add(normalize_origin(stored, bundle.root))
    return frozenset(origins)


def oracle_ingest_queue(layout: WorkspaceLayout) -> IngestQueueReport:
    """Terminal items whose `design` source is not yet recorded as ingested.
    Never writes, and never reads the clock.

    **Derived, never stored.** There is no frontmatter field, no migration and
    no second source of truth that can drift from `sources[]`. The predicate is
    exactly: terminal `work_status`, a `sources[]` entry with
    `id: design`, and no `Source` page whose (normalized) `origin` identifies
    that artifact.

    **Band 3 by necessity.** It needs the item's `sources[]`
    (`work-tracker-okf`) and the set of `Source` pages with their `origin`
    (`doc-wiki-okf`, which owns `normalize_origin`). Band-2 packages may not
    couple sideways, so the composition of two band-2 lanes belongs here --
    the same reason `graph_works_core.archive.commands` holds the work-item and
    wiki archive halves together.

    Read-only for the same reason `run_next` is: a queue that mutates while you
    look at it cannot be polled safely.
    """
    bundle = load_workspace_bundle(layout, ignore=IGNORE)
    ingested = _ingested_origins(bundle)
    pending: list[PendingIngest] = []
    for item in load_items(bundle):
        if item.work_status not in TERMINAL_STATUSES:
            continue
        resource = next(
            (source.resource for source in item.sources if source.id == SPEC_SOURCE_ID and source.resource),
            None,
        )
        if resource is None:
            continue
        origin = normalize_origin(str(layout.bundle_dir / resource.lstrip("/")), layout.bundle_dir)
        if origin in ingested:
            continue
        pending.append(PendingIngest(path=item.path, work_status=item.work_status, resource=resource, origin=origin))
    return IngestQueueReport(pending=tuple(sorted(pending, key=lambda entry: entry.path)))


def oracle_work_queue(layout: WorkspaceLayout) -> tuple[QueueEntry, ...]:
    """Route every active, non-terminal item as a dry-run `run_next` would. Never writes.

    The bundle and the dispatch config are each loaded once for the whole
    queue. A malformed dispatch file is each item's preflight blocker whenever
    its route offers a dispatch or a transition, exactly as `next` reports it,
    rather than a failure of the read.
    Epics waiting on their children are included with that blocker, so no
    active item is silently dropped.
    """
    bundle = load_work_bundle(layout)
    items = load_items(bundle)
    config = _load_config(layout)
    definition = _definition_of(config)
    entries: list[QueueEntry] = []
    for item in sorted(items, key=lambda candidate: candidate.path):
        if item.archived or item.work_status in TERMINAL_STATUSES:
            continue
        preview, _selected = _plan_route(
            layout, bundle.root, bundle.unreadable, items, item.path, descend=False, definition=definition
        )
        resolution, preflight = _resolve_dispatch_with(config, preview.state, preview.route)
        entries.append(QueueEntry(item, replace(preview, dispatch_resolution=resolution, dispatch_preflight=preflight)))
    return tuple(entries)


def oracle_open_decisions(layout: WorkspaceLayout) -> tuple[OpenDecision, ...]:
    """Every `status: open` entry in the ledger of every active item's decision owner. Never writes.

    Each owner's ledger is read once, and an absent ledger reads as empty, as
    `gw work decision list` does. Archived items are dropped before any ledger
    is read. `held` is the entry's `affects` paths that are active items whose
    own decision owner is this ledger's owner, in `affects` order: exactly the
    items `hold_for` would report this entry as holding.
    """
    bundle = load_work_bundle(layout)
    items = load_items(bundle)
    active = tuple(item for item in items if not item.archived)
    active_paths = {item.path for item in active}
    owners = sorted({owner for item in active if (owner := decision_owner(items, item.path)) is not None})
    found: list[OpenDecision] = []
    for owner in owners:
        ledger = _decisions.ledger_ref(owner).path(bundle.root)
        for entry in sorted(_decisions.load(ledger).entries, key=lambda decision: decision.number):
            if entry.status != "open":
                continue
            held = tuple(
                path for path in entry.affects if path in active_paths and decision_owner(items, path) == owner
            )
            found.append(OpenDecision(owner_path=owner, ledger=ledger, held=held, decision=entry))
    return tuple(found)

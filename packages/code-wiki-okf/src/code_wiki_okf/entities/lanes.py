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

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType

from code_graph_io import GraphReader
from okf_ext.generators import Render, plan_regenerate
from okf_ext.generators import apply as apply_regenerations
from okf_ext.schemas import SchemaSet, declared_directories
from okf_ext.shape import load_sections
from okf_io import Bundle, append_log_entry, load_bundle, update_index

from code_wiki_okf.config import Config
from code_wiki_okf.entities.catalog import render_repositories, repository_entries
from code_wiki_okf.entities.delete import prune_lane
from code_wiki_okf.entities.sync import sync_entities

#: Bundle-relative lane-segment names for the four types nested under
#: `repositories/<repo>/` -- Package, App, TestSuite, AgentPlugin. A repo's
#: own segment comes first (`f"repositories/{repo_name}/{lane}"`); these are
#: the segment `entities/pages.py::default_concept_id` appends after it.
REPO_SCOPED_LANES: tuple[str, ...] = ("packages/", "apps/", "test-suites/", "agent-plugins/")

#: Bundle-relative prefixes for the types that stay at the bundle root:
#: Dependency, ecosystem-wide by design (`entities/sync.py`'s own module
#: docstring). `"repositories/"` (the Repository page itself) is
#: deliberately absent -- it keeps its own separate exact-depth handling
#: throughout this module, same as before this lane split.
GLOBAL_LANES: tuple[str, ...] = ("dependencies/",)

#: Types `okf_ext.placement.rule`'s literal-prefix check cannot express once
#: nested: a per-repo path has no single static string its `directories:
#: Mapping[str, str]` contract can hold. Widening that contract is an
#: `okf_ext` (tier 2) change, out of scope for a code-wiki-okf-only item --
#: see `placement_directories`.
_UNCHECKABLE_PLACEMENT_TYPES = frozenset({"Package", "App", "TestSuite", "AgentPlugin"})


def is_entity_lane_page(concept_id: str) -> bool:
    """True for any page code-wiki-okf's entity lanes own: a global lane
    page, the Repository page itself, or a repo-scoped lane page nested
    under it. False for a mirror File page (`.../fs/...`) or anything else.

    Structural, not config-driven -- it matches on the fixed lane-segment
    vocabulary (`REPO_SCOPED_LANES`/`GLOBAL_LANES`) rather than a list of
    configured repo names, telling a Repository page apart from a mirror
    File page by depth rather than by knowing repo names. Public so
    `code_wiki_okf.sync.snapshot` and `graph_works_core.scan.commands` both
    make the same "is this an entity page" call the same way -- a lane
    change can never silently drift between the two.
    """
    if concept_id.startswith(GLOBAL_LANES):
        return True
    rest = concept_id.removeprefix("repositories/")
    if rest == concept_id:
        return False
    if "/" not in rest:
        return True  # the Repository page itself
    _repo, remainder = rest.split("/", 1)
    return remainder.startswith(REPO_SCOPED_LANES)


def placement_directories(schema_set: SchemaSet) -> dict[str, str]:
    """`okf_ext.schemas.declared_directories(schema_set)`, narrowed to the
    types a plain directory-prefix check can still express: Repository,
    File and Dependency. Package, App, TestSuite and AgentPlugin nest under
    `repositories/<repo>/` and are excluded -- see
    `_UNCHECKABLE_PLACEMENT_TYPES`.

    The one vocabulary both `code_wiki_okf.cli` and
    `graph_works_core.lint_drift.lanes` build their
    `placement_rule(...)` call from, so the exclusion list lives in one
    place instead of several literal copies that could drift apart.
    """
    return {
        type_name: directory
        for type_name, directory in declared_directories(schema_set).items()
        if type_name not in _UNCHECKABLE_PLACEMENT_TYPES
    }


#: The two types `x-okf-directory` alone cannot place, because both declare
#: `repositories/`: the `Repository` entity page lives directly under it, and
#: every mirror `File` page lives below that. The same distinction
#: `entities/delete.py`'s `exact_depth=` and `is_entity_lane_page` already make.
#:
#: It lives here, beside `REPO_SCOPED_LANES`/`GLOBAL_LANES`, so this module stays the single answer
#: to "what does this package know about its lanes" -- and it is passed into
#: `okf_ext.placement.placement_rule`, which holds no type names of its own.
ENTITY_DEPTH: Mapping[str, str] = MappingProxyType({"Repository": "exact", "File": "nested"})

#: The bundle-root directory id, the key `okf_io.Bundle.indexes` uses for it.
_ROOT = ""


@dataclass(frozen=True, slots=True)
class SyncSummary:
    """What one `sync()` run did, across every entity lane.

    `written` and `skipped` are `sync_entities`' own `EntitySync` fields,
    passed through unchanged. `deleted` and `declined` are `prune_lane`'s
    results pooled across every repo-scoped lane (per repo) and every global
    lane, plus the `repositories/` lane itself. `catalog` and
    `catalog_declined` are the root-index catalog pass's written members and
    its refusals -- reported rather than raised, matching how every other
    write failure in this pipeline surfaces.
    """

    written: tuple[str, ...] = field(default_factory=tuple)
    skipped: tuple[str, ...] = field(default_factory=tuple)
    deleted: tuple[str, ...] = field(default_factory=tuple)
    declined: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (concept_id, reason)
    catalog: tuple[str, ...] = field(default_factory=tuple)  # index members regenerated
    catalog_declined: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (path, kind)


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
    if summary.catalog:
        parts.append(f"catalog: {len(summary.catalog)} index page(s) regenerated")
    if summary.catalog_declined:
        named = ", ".join(f"{path} ({kind})" for path, kind in sorted(summary.catalog_declined))
        parts.append(f"catalog: {len(summary.catalog_declined)} index write(s) refused ({named})")
    return "; ".join(parts)


def _touched_directories(concept_id: str) -> tuple[str, ...]:
    """The directory ids whose `index.md` *concept_id* affects, when it is
    freshly written.

    A global-lane page or the Repository page itself touches exactly its
    own first path segment, same as before this lane split. A repo-scoped
    lane page (nested `repositories/<repo>/<lane>/<slug>`) touches both
    `repositories/<repo>` and `repositories/<repo>/<lane>` -- never the
    bundle-root `repositories` alone, which stays the Repository page's own
    territory (mirrors `mirror/apply.py::_mirror_directories`'s identical
    carve-out, and its own comment on why `repositories/<repo>/index.md` is
    legitimately reconciled by both lanes on their own runs).
    """
    parts = concept_id.split("/")
    if parts[0] != "repositories" or len(parts) <= 2:
        return (parts[0],)
    return (f"{parts[0]}/{parts[1]}", f"{parts[0]}/{parts[1]}/{parts[2]}")


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

    `sync_entities` runs once. Then `prune_lane` runs once per repo-scoped
    lane per configured repo (`REPO_SCOPED_LANES`, nested under
    `repositories/<repo>/`), once per global lane (`GLOBAL_LANES`), and once
    more for the `repositories/` lane itself (the Repository pages,
    `exact_depth=True` so a repo's own mirror subtree is never touched here).
    Each lane's deletion candidates are compared against
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

    Between deletion and reconciliation the root index's `## Repositories`
    catalog is regenerated from the reloaded bundle -- every Repository page
    it still holds, with each page's own `description` carried through. That
    ordering is load-bearing; see the comment at the call site.
    """
    if dry_run:
        return SyncSummary()

    entity_result = sync_entities(bundle, config, reader, today=today, at=at)

    # `sync_entities` already committed its writes; reload once so deletion
    # sees the pages it just created or regenerated.
    current = load_bundle(bundle.root)
    section_set = load_sections(config.declarations_dir / "_sections")
    should_exist = set(entity_result.current_resources)

    deleted: list[str] = []
    declined: list[tuple[str, str]] = []
    touched_lanes: set[str] = set()
    for repo_cfg in config.repos:
        prefix = f"repositories/{repo_cfg.name}"
        for lane in REPO_SCOPED_LANES:
            result = prune_lane(current, section_set, directory=f"{prefix}/{lane}", should_exist=should_exist)
            deleted.extend(result.deleted)
            declined.extend(result.declined)
            if result.deleted or result.declined:
                touched_lanes.add(prefix)
                touched_lanes.add(f"{prefix}/{lane.rstrip('/')}")
    for lane in GLOBAL_LANES:
        result = prune_lane(current, section_set, directory=lane, should_exist=should_exist)
        deleted.extend(result.deleted)
        declined.extend(result.declined)
        if result.deleted or result.declined:
            touched_lanes.add(lane.rstrip("/"))
    repositories_result = prune_lane(
        current, section_set, directory="repositories/", should_exist=should_exist, exact_depth=True
    )
    deleted.extend(repositories_result.deleted)
    declined.extend(repositories_result.declined)
    if repositories_result.deleted or repositories_result.declined:
        touched_lanes.add("repositories")
    for concept_id in entity_result.written:
        touched_lanes.update(_touched_directories(concept_id))

    # Reload again: `current` was walked before deletion, so its own
    # `.concepts` would still list every page `prune_lane` just removed from
    # disk, and `update_index` decides an entry is dead by asking
    # `bundle.has_member` -- which would still say "yes" against that now
    # stale snapshot.
    reconciled = load_bundle(current.root)

    # **Regenerate before reconcile, always.** Both writers touch the root
    # index body, and the ordering is what keeps them from fighting: after
    # regeneration the `## Repositories` bullets are correct, so
    # reconciliation finds nothing dead among them. Verified against
    # `okf_io.index`: a `/repositories/acme.md` bullet resolves (a leading `/`
    # is bundle-root-relative), so `_alive` says live and it is never pruned;
    # it does not satisfy `_covers` for the `repositories/` subdirectory
    # target, so the lane still gets its own `## Subdirectories` bullet; and
    # `_is_subdirectory_entry` is false for it, so `_sibling_heading` can
    # never anchor a newly added lane bullet inside `## Repositories`.
    #
    # No third reload: `generators.apply` commits through `set_body`, so
    # `reconciled`'s own root index document already agrees with disk by the
    # time `update_index` reads it.
    catalog_plan = plan_regenerate(
        reconciled,
        section_set,
        {},
        index_renders={_ROOT: Render(sections={"Repositories": render_repositories(repository_entries(reconciled))})},
    )
    catalog_result = apply_regenerations(reconciled, catalog_plan)

    # The root is always in the reconcile set: reconciliation's half of the
    # catalog is the lane list, and it is due whether or not a lane changed.
    update_index(reconciled, directories=sorted({*touched_lanes, _ROOT}), create_missing=True, dry_run=False)

    log_document = reconciled.logs.get("")
    if log_document is None:
        raise ValueError(
            f"{bundle.root}: no root log.md -- every code-wiki-okf bundle is created with one by "
            "okf_ext.bundle's scaffold"
        )

    summary = SyncSummary(
        written=entity_result.written,
        skipped=entity_result.skipped,
        deleted=tuple(deleted),
        declined=tuple(declined),
        catalog=catalog_result.written,
        catalog_declined=tuple((failure.path, failure.kind) for failure in catalog_result.failed),
    )
    append_log_entry(log_document, _summary_text(summary), today=today, dry_run=False)
    return summary


__all__ = [
    "ENTITY_DEPTH",
    "GLOBAL_LANES",
    "REPO_SCOPED_LANES",
    "SyncSummary",
    "is_entity_lane_page",
    "placement_directories",
    "sync",
]

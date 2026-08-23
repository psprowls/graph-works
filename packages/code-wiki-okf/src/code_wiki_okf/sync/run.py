"""Plan and apply one complete code-wiki sync without lane rediscovery."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from code_graph_io import GraphReader
from okf_ext.bundle import SECTIONS_DIRNAME
from okf_ext.generators import plan_regenerate
from okf_ext.sections import plan_sections
from okf_ext.shape import load_sections
from okf_ext.writing import ApplyResult
from okf_io import Bundle, Document, load_bundle

from code_wiki_okf.config import Config
from code_wiki_okf.entities.catalog import (
    CatalogEntry,
    CatalogPage,
    CatalogPlan,
    catalog_pages,
    plan_catalogs,
    reconcile_catalogs,
)
from code_wiki_okf.entities.delete import PruneResult, plan_prune_entities, prune_entities
from code_wiki_okf.entities.sync import (
    EntityPlan,
    EntityWrite,
    SyncSummary,
    _render_for_apply,
    apply_entities,
    plan_entities,
)
from code_wiki_okf.git_state import head_commit
from code_wiki_okf.mirror.apply import apply_mirror, preflight_mirror_live
from code_wiki_okf.mirror.model import MirrorPlan, MirrorResult
from code_wiki_okf.mirror.plan import plan_mirror
from code_wiki_okf.mirror.walk import tracked_files
from code_wiki_okf.placement import (
    PlacementError,
    affected_directories,
    context_from_resource,
    filesystem_member_identity,
)
from code_wiki_okf.resources import ResourceIndex, resource_index


@dataclass(frozen=True, slots=True)
class SyncPlan:
    entities: EntityPlan
    mirrors: tuple[MirrorPlan, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MirrorSummary:
    plans: tuple[MirrorPlan, ...] = field(default_factory=tuple)
    results: tuple[MirrorResult, ...] = field(default_factory=tuple)
    skipped_repos: tuple[str, ...] = field(default_factory=tuple)
    failed_repos: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.failed_repos

    @property
    def created(self) -> int:
        return sum(len(result.created) for result in self.results)

    @property
    def regenerated(self) -> int:
        return sum(len(result.regenerated) for result in self.results)

    @property
    def moved(self) -> int:
        return sum(len(result.moved) for result in self.results)

    @property
    def deleted(self) -> int:
        return sum(len(result.deleted) for result in self.results)

    @property
    def declined(self) -> int:
        return sum(len(result.declined_deletions) for result in self.results)

    @property
    def stranded(self) -> int:
        return sum(len(plan.moves.stranded) for plan in self.plans)


@dataclass(frozen=True, slots=True)
class SyncResult:
    entities: SyncSummary
    mirror: MirrorSummary
    warnings: tuple[str, ...]
    dry_run: bool

    @property
    def ok(self) -> bool:
        return self.entities.ok and self.mirror.ok


def _at_datetime(at: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(at)
    except ValueError as exc:
        raise ValueError(f"at must be an ISO-8601 instant, got {at!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("at must include a timezone offset")
    return parsed


def _preflight_union(entities: EntityPlan, mirrors: tuple[MirrorPlan, ...]) -> None:
    resources_by_member: dict[str, tuple[str, str]] = {}

    def claim(member: str, resource: str) -> None:
        identity = filesystem_member_identity(member)
        prior = resources_by_member.get(identity)
        if prior is not None and prior[1] != resource:
            raise PlacementError(
                resource=resource,
                reason=(
                    f"planned member {member} is filesystem-equivalent to {prior[0]}, which also belongs to {prior[1]}"
                ),
                expected=member.removesuffix(".md"),
            )
        resources_by_member[identity] = (member, resource)

    for write in entities.writes:
        claim(write.member, write.context.resource)
    for mirror in mirrors:
        for target in mirror.targets:
            claim(target.member, target.resource)


def _preflight_live_entity_filesystem_conflicts(bundle_root: Path, entities: EntityPlan) -> None:
    """Refuse live entity target drift before either lane writes."""
    index = resource_index(load_bundle(bundle_root))
    for write in entities.writes:
        member = write.member
        resource = write.context.resource
        path_conflicts = index.filesystem_path_conflicts_for(member)
        if path_conflicts:
            raise PlacementError(
                resource=resource,
                reason=(
                    f"planned member {member} has a filesystem-equivalent path type conflict with occupied "
                    f"{', '.join(path_conflicts)}; re-plan"
                ),
                expected=member.removesuffix(".md"),
            )
        equivalent_members = index.filesystem_members_for(member)
        if equivalent_members and equivalent_members != (member,):
            raise PlacementError(
                resource=resource,
                reason=(
                    f"planned member {member} is filesystem-equivalent to occupied "
                    f"{', '.join(equivalent_members)}; re-plan"
                ),
                expected=member.removesuffix(".md"),
            )


def _existing_file_resources(index: ResourceIndex, repository: str) -> frozenset[str]:
    """File resources a skipped repository already owns on disk."""
    found: set[str] = set()
    for resource, entry in index.by_resource.items():
        if entry.document.fm.type != "File":
            continue
        try:
            context = context_from_resource("File", resource)
        except PlacementError:
            continue
        if context.repository == repository:
            found.add(resource)
    return frozenset(found)


def plan_sync(bundle_root: Path, *, config: Config, reader: GraphReader, at: str) -> SyncPlan:
    """Build and cross-check the complete immutable entity/File plan."""
    generated_at = _at_datetime(at)
    bundle = load_bundle(bundle_root)
    index = resource_index(bundle)
    entities = plan_entities(bundle, reader, config, at=at, index=index)

    walked = tracked_files(config)
    mirrors: list[MirrorPlan] = []
    warnings = list(entities.warnings)
    current_resources = set(entities.current_resources)
    for repo in config.repos:
        sha = head_commit(repo.path)
        if sha is None:
            warnings.append(f"{repo.name}: not a git checkout, skipping")
            current_resources.update(_existing_file_resources(index, repo.name))
            continue
        mirror = plan_mirror(
            bundle,
            reader,
            repo,
            tracked=walked[repo.name],
            sha=sha,
            at=generated_at,
            index=index,
        )
        mirrors.append(mirror)
        current_resources.update(mirror.target_for(source_path).resource for source_path in walked[repo.name])
        warnings.extend(
            f"{mirror.repo}: deletion declined for {item.path} ({item.reason})" for item in mirror.declined_deletions
        )

    frozen_mirrors = tuple(mirrors)
    entities = replace(entities, current_resources=frozenset(current_resources))
    _preflight_union(entities, frozen_mirrors)
    return SyncPlan(entities=entities, mirrors=frozen_mirrors, warnings=tuple(sorted(set(warnings))))


def summary_for_entity_plan(
    bundle: Bundle,
    plan: EntityPlan,
    *,
    declarations_dir: Path | None = None,
) -> SyncSummary:
    """Preview only entity creates and content-bearing updates."""
    declarations_root = bundle.root if declarations_dir is None else declarations_dir
    section_set = load_sections(declarations_root / SECTIONS_DIRNAME)
    created = tuple(
        write.member.removesuffix(".md")
        for write in plan.writes
        if bundle.concept(write.member.removesuffix(".md")) is None
    )
    existing_ids = {
        write.member.removesuffix(".md")
        for write in plan.writes
        if bundle.concept(write.member.removesuffix(".md")) is not None
    }
    scaffold = plan_sections(bundle, section_set)
    scaffold_updates = {splice.concept_id for splice in scaffold.splices if splice.concept_id in existing_ids}
    content_renders = {
        write.member.removesuffix(".md"): _render_for_apply(write, content_only=True)
        for write in plan.writes
        if write.member.removesuffix(".md") in existing_ids
    }
    regeneration = plan_regenerate(bundle, section_set, content_renders)
    update_ids = scaffold_updates | set(regeneration.concept_ids)
    updated = tuple(
        write.member.removesuffix(".md") for write in plan.writes if write.member.removesuffix(".md") in update_ids
    )
    return SyncSummary(
        created=created,
        updated=updated,
        written=tuple((*created, *updated)),
        warnings=plan.warnings,
    )


def _catalog_page_for_entity(write: EntityWrite, existing: CatalogPage | None) -> CatalogPage:
    context = write.context
    concept_id = write.member.removesuffix(".md")
    title = str(write.frontmatter["title"])
    description = existing.entry.description if existing is not None else ""
    owned = (
        {"package_count": write.frontmatter["package_count"]}
        if context.type_name == "Repository" and "package_count" in write.frontmatter
        else {}
    )
    return CatalogPage(
        type_name=context.type_name,
        context=context,
        entry=CatalogEntry(title=title, concept_id=concept_id, description=description),
        owned_frontmatter=MappingProxyType(owned),
    )


def _project_catalog_pages(bundle: Bundle, plan: SyncPlan, prune: PruneResult) -> tuple[CatalogPage, ...]:
    """Project post-mirror/post-prune catalog membership without writes."""
    actual = catalog_pages(bundle)
    actual_by_concept = {page.entry.concept_id: page for page in actual}
    pages = {page.context.resource: page for page in actual}

    removed_concepts = set(prune.deleted)
    for mirror in plan.mirrors:
        removed_concepts.update(move.source.removesuffix(".md") for move in mirror.moves.moves)
        removed_concepts.update(
            mirror.target_for(source_path).member.removesuffix(".md") for source_path in mirror.deletions
        )
    for resource, page in tuple(pages.items()):
        if page.entry.concept_id in removed_concepts:
            del pages[resource]

    for write in plan.entities.writes:
        existing = pages.get(write.context.resource)
        pages[write.context.resource] = _catalog_page_for_entity(write, existing)

    for mirror in plan.mirrors:
        for source_path, (frontmatter, _render) in mirror.creates.items():
            target = mirror.target_for(source_path)
            context = context_from_resource("File", target.resource)
            pages[target.resource] = CatalogPage(
                type_name="File",
                context=context,
                entry=CatalogEntry(
                    title=str(frontmatter["title"]),
                    concept_id=target.member.removesuffix(".md"),
                    description="",
                ),
                owned_frontmatter=MappingProxyType({}),
            )
        for move in mirror.moves.moves:
            source = actual_by_concept.get(move.source.removesuffix(".md"))
            target = next(item for item in mirror.targets if item.member == move.dest)
            context = context_from_resource("File", target.resource)
            pages[target.resource] = CatalogPage(
                type_name="File",
                context=context,
                entry=CatalogEntry(
                    title=PurePosixPath(target.source_path).name,
                    concept_id=target.member.removesuffix(".md"),
                    description=source.entry.description if source is not None else "",
                ),
                owned_frontmatter=MappingProxyType({}),
            )
    return tuple(sorted(pages.values(), key=lambda page: page.entry.concept_id))


def _mirror_removed_concepts(mirrors: tuple[MirrorPlan, ...]) -> frozenset[str]:
    removed: set[str] = set()
    for mirror in mirrors:
        removed.update(move.source.removesuffix(".md") for move in mirror.moves.moves)
        removed.update(mirror.target_for(path).member.removesuffix(".md") for path in mirror.deletions)
    return frozenset(removed)


def _project_mirror_indexes(bundle: Bundle, mirrors: tuple[MirrorPlan, ...]) -> Bundle:
    """Project indexes that non-empty mirror application creates before catalogs."""
    indexes = dict(bundle.indexes)
    for mirror in mirrors:
        if mirror.is_empty:
            continue
        for target in mirror.targets:
            context = context_from_resource("File", target.resource)
            for directory in affected_directories(context):
                if directory in indexes:
                    continue
                member = f"{directory}/index.md" if directory else "index.md"
                indexes[directory] = Document.parse("", path=bundle.root / member)
    return replace(bundle, indexes=MappingProxyType(indexes))


def combine_results(
    entities: SyncSummary,
    mirror: MirrorSummary,
    prune_result: PruneResult,
    catalogs: ApplyResult,
    catalog_plan: CatalogPlan,
    warnings: tuple[str, ...],
) -> SyncResult:
    """Construct the wet-run result from already-computed lane results."""
    return SyncResult(
        entities=replace(
            entities,
            deleted=prune_result.deleted,
            declined=prune_result.declined,
            catalog=catalogs.written,
            catalog_created=tuple(path for path in catalog_plan.created if path in catalogs.written),
            catalog_updated=tuple(path for path in catalog_plan.updated if path in catalogs.written),
            catalog_declined=(
                tuple((failure.path, failure.kind) for failure in catalogs.failed)
                + tuple((item.path, item.reason) for item in catalogs.skipped)
            ),
            warnings=warnings,
        ),
        mirror=mirror,
        warnings=warnings,
        dry_run=False,
    )


def sync_bundle(
    bundle_root: Path,
    *,
    config: Config,
    reader: GraphReader,
    at: str,
    today: date,
    dry_run: bool = False,
) -> SyncResult:
    """Apply only a completely planned sync, then prune against its resource set."""
    plan = plan_sync(bundle_root, config=config, reader=reader, at=at)
    bundle = load_bundle(bundle_root)
    skipped_repos = tuple(repo.name for repo in config.repos if repo.name not in {item.repo for item in plan.mirrors})
    if dry_run:
        entities = summary_for_entity_plan(bundle, plan.entities, declarations_dir=config.declarations_dir)
        raw_prune = plan_prune_entities(
            bundle,
            plan.entities.current_resources,
            declarations_dir=config.declarations_dir,
        )
        mirror_removed = _mirror_removed_concepts(plan.mirrors)
        prune = replace(raw_prune, deleted=tuple(item for item in raw_prune.deleted if item not in mirror_removed))
        catalog_bundle = _project_mirror_indexes(bundle, plan.mirrors)
        catalog_plan = plan_catalogs(
            catalog_bundle,
            pages=_project_catalog_pages(bundle, plan, raw_prune),
            declarations_dir=config.declarations_dir,
        )
        return SyncResult(
            entities=replace(
                entities,
                deleted=prune.deleted,
                declined=prune.declined,
                catalog=tuple(dict.fromkeys((*catalog_plan.created, *catalog_plan.updated))),
                catalog_created=catalog_plan.created,
                catalog_updated=catalog_plan.updated,
                catalog_declined=catalog_plan.declined,
                warnings=plan.warnings,
            ),
            mirror=MirrorSummary(plans=plan.mirrors, skipped_repos=skipped_repos),
            warnings=plan.warnings,
            dry_run=True,
        )

    _preflight_live_entity_filesystem_conflicts(bundle_root, plan.entities)
    for mirror_plan in plan.mirrors:
        if not mirror_plan.is_empty:
            preflight_mirror_live(bundle_root, mirror_plan)
    entities = apply_entities(
        bundle_root,
        plan.entities,
        today=today,
        declarations_dir=config.declarations_dir,
    )
    results: list[MirrorResult] = []
    failed: list[tuple[str, str]] = []
    for item in plan.mirrors:
        if item.is_empty:
            results.append(
                MirrorResult(
                    repo=item.repo,
                    moved=(),
                    created=(),
                    regenerated=(),
                    deleted=(),
                    declined_deletions=item.declined_deletions,
                    index_updates=(),
                )
            )
            continue
        try:
            result = apply_mirror(
                bundle_root,
                item,
                today=today,
                declarations_dir=config.declarations_dir,
            )
            results.append(result)
            if result.failed:
                failed.append((item.repo, "; ".join(result.failed)))
        except Exception as exc:  # a partial application is returned and fails the CLI
            failed.append((item.repo, str(exc)))
    mirror = MirrorSummary(
        plans=plan.mirrors,
        results=tuple(results),
        skipped_repos=skipped_repos,
        failed_repos=tuple(failed),
    )
    prune_result = prune_entities(
        load_bundle(bundle_root),
        plan.entities.current_resources,
        declarations_dir=config.declarations_dir,
    )
    post_prune = load_bundle(bundle_root)
    catalog_plan = plan_catalogs(post_prune, declarations_dir=config.declarations_dir)
    catalogs = reconcile_catalogs(post_prune, today=today, declarations_dir=config.declarations_dir)
    return combine_results(entities, mirror, prune_result, catalogs, catalog_plan, plan.warnings)


__all__ = [
    "MirrorSummary",
    "SyncPlan",
    "SyncResult",
    "combine_results",
    "plan_sync",
    "summary_for_entity_plan",
    "sync_bundle",
]

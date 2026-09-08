"""Plan every entity placement, then apply that immutable plan.

The planner is the only graph-reading half of this module. It resolves every
Repository, repository-owned entity, and Dependency through
``code_wiki_okf.placement``; checks duplicate, wrong-path, occupied-path, and
target-collision failures; and returns complete bundle members. The applier
receives no reader or workspace config and therefore cannot rediscover or
silently redirect a target after preflight.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from code_graph_io import GraphReader, NodeRecord
from okf_ext.bundle import SCHEMA_DIRNAME, SECTIONS_DIRNAME
from okf_ext.generators import Render, plan_regenerate
from okf_ext.generators import apply as apply_regenerations
from okf_ext.schemas import load_schemas
from okf_ext.sections import apply as apply_sections
from okf_ext.sections import plan_sections
from okf_ext.shape import load_sections
from okf_io import Bundle, load_bundle

from code_wiki_okf import __version__
from code_wiki_okf.config import Config
from code_wiki_okf.entities.pages import new_page_text
from code_wiki_okf.entities.render import (
    render_agent_plugin,
    render_app,
    render_dependency,
    render_package,
    render_repository,
    render_test_suite,
)
from code_wiki_okf.git_state import head_commit
from code_wiki_okf.placement import (
    PlacementContext,
    PlacementError,
    canonical_member,
    context_from_resource,
    filesystem_member_identity,
)
from code_wiki_okf.provenance import generated_value, last_updated_commit_value
from code_wiki_okf.resources import ResourceIndex, resource_index

_PROVENANCE_KEYS = frozenset({"generated", "last_updated_commit", "tokens"})
_UNIVERSAL_KEYS = frozenset({"type", "title", "resource", "description"})


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [_thaw(item) for item in sorted(value, key=repr)]
    return value


@dataclass(frozen=True, slots=True)
class _PlannedFrontmatter(Mapping[str, object]):
    """Immutable mapping view plus the generated body owned by one render.

    ``EntityWrite`` intentionally exposes only its frontmatter mapping. This
    private mapping retains the corresponding generated sections so the plan
    remains self-contained without widening the public plan shape or asking
    the applier to query the graph again.
    """

    _values: Mapping[str, object]
    sections: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_values",
            MappingProxyType({key: _freeze(value) for key, value in self._values.items()}),
        )
        object.__setattr__(self, "sections", MappingProxyType(dict(self.sections)))

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


@dataclass(frozen=True, slots=True)
class EntityWrite:
    context: PlacementContext
    member: str
    frontmatter: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class EntityPlan:
    writes: tuple[EntityWrite, ...]
    current_resources: frozenset[str]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SyncSummary:
    """Entity application result consumed by the composite sync task."""

    created: tuple[str, ...] = field(default_factory=tuple)
    updated: tuple[str, ...] = field(default_factory=tuple)
    written: tuple[str, ...] = field(default_factory=tuple)
    skipped: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    deleted: tuple[str, ...] = field(default_factory=tuple)
    declined: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    deleted_lane_residue: tuple[str, ...] = field(default_factory=tuple)
    catalog: tuple[str, ...] = field(default_factory=tuple)
    catalog_created: tuple[str, ...] = field(default_factory=tuple)
    catalog_updated: tuple[str, ...] = field(default_factory=tuple)
    catalog_declined: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.skipped and not self.catalog_declined


def _repo_uris_by_name(reader: GraphReader) -> Mapping[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for node in reader.list_repositories():
        uri = node.attrs.get("uri")
        if uri:
            grouped.setdefault(node.name, []).append(str(uri))
    return MappingProxyType({name: tuple(sorted(uris)) for name, uris in sorted(grouped.items())})


def _nodes_for_repo(nodes: Sequence[NodeRecord], repo_uri: str) -> list[NodeRecord]:
    return [node for node in nodes if node.attrs.get("repo") == repo_uri]


def _resource_text(node: NodeRecord) -> str:
    uri = node.attrs.get("uri")
    if not uri:
        raise PlacementError(resource=f"{node.kind}:{node.name}", reason="graph node carries no resource URI")
    return str(uri)


def _repository_name(context: PlacementContext) -> str:
    if context.repository is None:
        raise PlacementError(resource=context.resource, reason="placement context has no repository")
    return context.repository


def _dependency_identity(context: PlacementContext) -> tuple[str, str]:
    if context.ecosystem is None or context.name is None:
        raise PlacementError(resource=context.resource, reason="placement context has no dependency identity")
    return context.ecosystem, context.name


def _require_description[T](value: T | None, *, resource: str) -> T:
    if value is None:
        raise PlacementError(resource=resource, reason="graph listed this node but cannot describe it")
    return value


def _at_datetime(at: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(at)
    except ValueError as exc:
        raise ValueError(f"at must be an ISO-8601 instant, got {at!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("at must include a timezone offset")
    return parsed


def _stamp_provenance(render: Render, *, sha: str | None, at: datetime) -> Render:
    frontmatter = dict(render.frontmatter)
    frontmatter["generated"] = generated_value(by=f"code-wiki-okf/{__version__}", at=at)
    if sha:
        frontmatter["last_updated_commit"] = last_updated_commit_value(sha)
    return Render(frontmatter=frontmatter, sections=render.sections)


def _write(context: PlacementContext, *, title: str, render: Render) -> EntityWrite:
    frontmatter: dict[str, object] = {
        "type": context.type_name,
        "title": title,
        "resource": context.resource,
        **render.frontmatter,
    }
    return EntityWrite(
        context=context,
        member=canonical_member(context),
        frontmatter=_PlannedFrontmatter(frontmatter, render.sections),
    )


def _duplicate_error(index: ResourceIndex) -> PlacementError | None:
    duplicates = {resource: members for resource, members in index.members_by_resource.items() if len(members) > 1}
    if not duplicates:
        return None
    resource, members = min(duplicates.items())
    return PlacementError(
        resource=resource,
        reason=f"duplicate resource in {', '.join(sorted(members))}; delete duplicate pre-release pages and regenerate",
    )


def _preflight_existing(bundle: Bundle, index: ResourceIndex, writes: Sequence[EntityWrite]) -> None:
    for write in writes:
        actual = index.member_for(write.context.resource)
        expected = write.member.removesuffix(".md")
        path_conflicts = index.filesystem_path_conflicts_for(write.member)
        if path_conflicts:
            raise PlacementError(
                resource=write.context.resource,
                reason=(
                    f"canonical member {write.member} has a filesystem-equivalent path type conflict with existing "
                    f"{', '.join(path_conflicts)}; delete the old pre-release page and regenerate"
                ),
                expected=expected,
            )
        if actual is not None and actual != write.member:
            raise PlacementError(
                resource=write.context.resource,
                reason=f"found at {actual}; delete the old pre-release page and regenerate",
                expected=expected,
            )
        entry = index.get(write.context.resource)
        if entry is not None:
            actual_type = (entry.document.fm.type or "").strip()
            if actual_type != write.context.type_name:
                raise PlacementError(
                    resource=write.context.resource,
                    reason=(
                        f"{write.member} declares type {actual_type or '(blank)'} instead of "
                        f"{write.context.type_name}; delete the old pre-release page and regenerate"
                    ),
                    expected=expected,
                )
        equivalent_members = index.filesystem_members_for(write.member)
        if equivalent_members and equivalent_members != (write.member,):
            raise PlacementError(
                resource=write.context.resource,
                reason=(
                    f"canonical member {write.member} is filesystem-equivalent to existing "
                    f"{', '.join(equivalent_members)}; delete the old pre-release page and regenerate"
                ),
                expected=expected,
            )
        if entry is None and (bundle.root / write.member).exists():
            raise PlacementError(
                resource=write.context.resource,
                reason=(
                    f"found an existing page at {write.member} with no matching resource; "
                    "delete the old pre-release page and regenerate"
                ),
                expected=expected,
            )


def plan_entities(
    bundle: Bundle,
    reader: GraphReader,
    config: Config,
    *,
    at: str,
    index: ResourceIndex | None = None,
) -> EntityPlan:
    """Resolve and preflight every desired entity without touching disk."""
    index = resource_index(bundle) if index is None else index
    duplicate_error = _duplicate_error(index)
    if duplicate_error is not None:
        raise duplicate_error

    generated_at = _at_datetime(at)
    repo_uris = _repo_uris_by_name(reader)
    all_packages = reader.list_packages()
    all_apps = reader.list_apps()
    all_suites = reader.list_test_suites()
    all_plugins = reader.list_agent_plugins()

    writes: list[EntityWrite] = []
    warnings: list[str] = []
    resource_by_member: dict[str, tuple[str, str]] = {}

    def add(write: EntityWrite) -> None:
        if PurePosixPath(write.member).name == "index.md":
            raise PlacementError(
                resource=write.context.resource,
                reason="entity page would collide with a reserved directory index.md",
                expected=write.member.removesuffix(".md"),
            )
        identity = filesystem_member_identity(write.member)
        prior = resource_by_member.get(identity)
        if prior is not None and prior[1] != write.context.resource:
            raise PlacementError(
                resource=write.context.resource,
                reason=(
                    f"canonical member {write.member} is filesystem-equivalent to {prior[0]}, "
                    f"which also belongs to {prior[1]}"
                ),
                expected=write.member.removesuffix(".md"),
            )
        resource_by_member[identity] = (write.member, write.context.resource)
        writes.append(write)

    for repo_config in config.repos:
        matching_repo_uris = repo_uris.get(repo_config.name, ())
        if not matching_repo_uris:
            continue
        if len(matching_repo_uris) > 1:
            resource = matching_repo_uris[0]
            context = context_from_resource("Repository", resource)
            raise PlacementError(
                resource=resource,
                reason=f"repository name {repo_config.name!r} is claimed by {', '.join(matching_repo_uris)}",
                expected=canonical_member(context).removesuffix(".md"),
            )
        repo_uri = matching_repo_uris[0]
        sha = head_commit(repo_config.path)

        for node in _nodes_for_repo(all_packages, repo_uri):
            context = context_from_resource("Package", _resource_text(node))
            package_description = _require_description(
                reader.describe_package(name=node.name, uri=context.resource),
                resource=context.resource,
            )
            render = _stamp_provenance(
                render_package(package_description, repo_name=_repository_name(context)),
                sha=sha,
                at=generated_at,
            )
            add(_write(context, title=node.name, render=render))

        for node in _nodes_for_repo(all_apps, repo_uri):
            context = context_from_resource("App", _resource_text(node))
            app_description = _require_description(
                reader.describe_app(name=node.name, uri=context.resource),
                resource=context.resource,
            )
            render = _stamp_provenance(
                render_app(app_description, repo_name=_repository_name(context)),
                sha=sha,
                at=generated_at,
            )
            add(_write(context, title=node.name, render=render))

        for node in _nodes_for_repo(all_suites, repo_uri):
            context = context_from_resource("TestSuite", _resource_text(node))
            suite_description = _require_description(
                reader.describe_test_suite(suite_name=node.name, uri=context.resource),
                resource=context.resource,
            )
            tested_packages = reader.consumer_packages(kind="test_suite", entity_uri=suite_description.uri)
            render = _stamp_provenance(
                render_test_suite(
                    suite_description,
                    tested_packages=tested_packages,
                    repo_name=_repository_name(context),
                ),
                sha=sha,
                at=generated_at,
            )
            add(_write(context, title=node.name, render=render))

        for node in _nodes_for_repo(all_plugins, repo_uri):
            context = context_from_resource("AgentPlugin", _resource_text(node))
            plugin_description = _require_description(
                reader.describe_agent_plugin(name=node.name, uri=context.resource),
                resource=context.resource,
            )
            render = _stamp_provenance(
                render_agent_plugin(plugin_description, repo_name=_repository_name(context)),
                sha=sha,
                at=generated_at,
            )
            add(_write(context, title=node.name, render=render))

        package_count = len(_nodes_for_repo(all_packages, repo_uri))
        repository_context = context_from_resource("Repository", repo_uri)
        repository_render = _stamp_provenance(
            render_repository(package_count=package_count),
            sha=sha,
            at=generated_at,
        )
        add(_write(repository_context, title=_repository_name(repository_context), render=repository_render))

    for node in reader.list_dependencies():
        context = context_from_resource("Dependency", _resource_text(node))
        ecosystem, dependency_name = _dependency_identity(context)
        dependency_description = _require_description(
            reader.describe_dependency(ecosystem=ecosystem, name=dependency_name),
            resource=context.resource,
        )
        implementations = sorted(dependency_description.implemented_by)
        if len(implementations) > 1:
            warnings.append(f"{context.resource} has multiple implementations: {', '.join(implementations)}")
        if implementations:
            # A Dependency whose node resolves to a workspace member gets no
            # page (ADR-0034 as narrowed by ADR-0048): `used_by` and
            # `versions_in_use` move onto the implementing Package page
            # instead. The graph node and its `implemented_by` edge are
            # unaffected — only the write is skipped.
            continue
        dependency_description = replace(dependency_description, implemented_by=implementations)
        render = _stamp_provenance(render_dependency(dependency_description), sha=None, at=generated_at)
        add(_write(context, title=dependency_name, render=render))

    ordered_writes = tuple(sorted(writes, key=lambda write: (write.member, write.context.resource)))
    _preflight_existing(bundle, index, ordered_writes)
    return EntityPlan(
        writes=ordered_writes,
        current_resources=frozenset(write.context.resource for write in ordered_writes),
        warnings=tuple(sorted(warnings)),
    )


def _render_for_apply(write: EntityWrite, *, content_only: bool) -> Render:
    frontmatter = {
        key: _thaw(value)
        for key, value in write.frontmatter.items()
        if key not in _UNIVERSAL_KEYS and (not content_only or key not in _PROVENANCE_KEYS)
    }
    sections = write.frontmatter.sections if isinstance(write.frontmatter, _PlannedFrontmatter) else {}
    return Render(frontmatter=frontmatter, sections=sections)


def apply_entities(
    bundle_root: Path,
    plan: EntityPlan,
    *,
    today: date,
    declarations_dir: Path | None = None,
) -> SyncSummary:
    """Apply exactly *plan*'s members, without consulting the graph or config."""
    _ = today
    members: dict[str, tuple[str, str]] = {}
    for write in plan.writes:
        canonical = canonical_member(write.context)
        if canonical != write.member:
            raise PlacementError(
                resource=write.context.resource,
                reason=f"planned member {write.member} is not canonical",
                expected=canonical.removesuffix(".md"),
            )
        identity = filesystem_member_identity(write.member)
        prior = members.get(identity)
        if prior is not None and prior[1] != write.context.resource:
            raise PlacementError(
                resource=write.context.resource,
                reason=(
                    f"planned member {write.member} is filesystem-equivalent to {prior[0]}, "
                    f"which also belongs to {prior[1]}"
                ),
                expected=write.member.removesuffix(".md"),
            )
        members[identity] = (write.member, write.context.resource)

    bundle = load_bundle(bundle_root)
    index = resource_index(bundle)
    for write in plan.writes:
        path_conflicts = index.filesystem_path_conflicts_for(write.member)
        if path_conflicts:
            raise PlacementError(
                resource=write.context.resource,
                reason=(
                    f"planned member {write.member} has a filesystem-equivalent path type conflict with occupied "
                    f"{', '.join(path_conflicts)}; re-plan"
                ),
                expected=write.member.removesuffix(".md"),
            )
        equivalent_members = index.filesystem_members_for(write.member)
        if equivalent_members and equivalent_members != (write.member,):
            raise PlacementError(
                resource=write.context.resource,
                reason=(
                    f"planned member {write.member} is filesystem-equivalent to occupied "
                    f"{', '.join(equivalent_members)}; re-plan"
                ),
                expected=write.member.removesuffix(".md"),
            )

    declarations_root = bundle_root if declarations_dir is None else declarations_dir
    schema_set = load_schemas(declarations_root / SCHEMA_DIRNAME)
    section_set = load_sections(declarations_root / SECTIONS_DIRNAME)
    created_ids = tuple(
        write.member.removesuffix(".md")
        for write in plan.writes
        if bundle.concept(write.member.removesuffix(".md")) is None
    )

    for write in plan.writes:
        concept_id = write.member.removesuffix(".md")
        if bundle.concept(concept_id) is not None:
            continue
        text = new_page_text(
            schema_set=schema_set,
            section_set=section_set,
            type_name=write.context.type_name,
            title=str(write.frontmatter["title"]),
            resource=write.context.resource,
        )
        path = bundle_root / write.member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")

    working_bundle = load_bundle(bundle_root)
    planned_ids = {write.member.removesuffix(".md") for write in plan.writes}
    scaffold = plan_sections(working_bundle, section_set)
    scaffold_splices = tuple(splice for splice in scaffold.splices if splice.concept_id in planned_ids)
    scaffold_failed: tuple[str, ...] = ()
    if scaffold_splices:
        scaffold_result = apply_sections(
            working_bundle,
            replace(scaffold, splices=scaffold_splices, skipped=()),
        )
        scaffold_failed = tuple(
            f"{failure.path}: {failure.kind}: {failure.error}" for failure in scaffold_result.failed
        )

    content_renders = {
        write.member.removesuffix(".md"): _render_for_apply(write, content_only=True) for write in plan.writes
    }
    content_plan = plan_regenerate(working_bundle, section_set, content_renders)
    stale_ids = frozenset(content_plan.concept_ids)
    renders = {
        write.member.removesuffix(".md"): _render_for_apply(write, content_only=False)
        for write in plan.writes
        if write.member.removesuffix(".md") in stale_ids
    }
    regeneration_plan = plan_regenerate(working_bundle, section_set, renders)
    result = apply_regenerations(working_bundle, regeneration_plan)

    regenerated = tuple(member.removesuffix(".md") for member in result.written)
    created_set = set(created_ids)
    updated = tuple(concept_id for concept_id in regenerated if concept_id not in created_set)
    written = tuple(dict.fromkeys((*created_ids, *updated)))
    return SyncSummary(
        created=created_ids,
        updated=updated,
        written=written,
        skipped=(
            scaffold_failed
            + tuple(f"{failure.path}: {failure.kind}: {failure.error}" for failure in result.failed)
            + tuple(f"{item.path}: {item.reason}" for item in result.skipped)
        ),
        warnings=plan.warnings,
    )


__all__ = [
    "EntityPlan",
    "EntityWrite",
    "SyncSummary",
    "apply_entities",
    "plan_entities",
]

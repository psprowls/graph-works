"""The GraphReader-driven entity-lane orchestrator.

Enumerate -> resolve each entity to an existing-or-new page target -> create
new pages on disk -> reload the bundle -> stamp provenance -> plan_regenerate
+ apply. Two phases (create-then-regenerate) because `plan_regenerate` only
edits documents already in `bundle.concepts`; it creates nothing
(`entities/pages.py`'s own docstring makes the same point about
`new_page_text`).

Dependency is enumerated ecosystem-wide, no repo filter: a dependency node is
one of code-graph-io's `_GLOBAL_KINDS` (`upsert.py`) and is never
repo-attributed, so there is no per-repo set to filter it against, and
`dependencies/ruamel.yaml.md` aggregating `used_by` across every consuming
repo is the point.

Repository does **not** use `GraphReader.describe_repository()`: that method
guarantees exactly one Repository row per DB (`queries.describe_repository`'s
own docstring), which held for a single-repo graph but not for the shared
multi-repo one this orchestrator reads. `package_count` is computed per repo
instead, by filtering `list_packages()` down to that repo's attribution
(the `repo` column code-graph-io's `upsert.py` stamps on every path-bearing,
non-global-kind node while `set_current_repo` is active for that member).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from code_graph_io import GraphReader, NodeRecord
from code_graph_io.tokens import count_tokens
from okf_ext.generators import Render, plan_regenerate
from okf_ext.generators import apply as apply_regenerations
from okf_ext.schemas import SchemaSet, load_schemas
from okf_ext.shape import load_sections
from okf_io import Bundle, load_bundle

from code_wiki_okf import __version__
from code_wiki_okf.config import Config
from code_wiki_okf.entities.pages import default_concept_id, new_page_text
from code_wiki_okf.entities.render import (
    render_agent_plugin,
    render_app,
    render_dependency,
    render_package,
    render_repository,
    render_test_suite,
)
from code_wiki_okf.git_state import head_commit
from code_wiki_okf.provenance import generated_value, last_updated_commit_value, tokens_value
from code_wiki_okf.resources import resource_index


@dataclass(frozen=True, slots=True)
class EntitySync:
    """What `sync_entities` did.

    `written` is bundle-relative concept ids (no `.md`), matching
    `Bundle.concepts`' own keys -- `okf_ext.writing.ApplyResult.written`
    carries the `.md` member path instead, and this package's convention
    (`resources.py`, `pages.py`) is the suffix-less id, so the suffix is
    stripped once here rather than leaking to every caller.

    `current_resources` is every `resource:` string this run resolved to a
    live target -- the `.resource` of each `_Target` in `placed` (`placed` is
    keyed by `concept_id`, so this is read off its values, not its keys) --
    whether or not that target's page actually needed a write. This is
    deliberately wider than `written`: a deletion caller
    (`entities.lanes.sync`) needs "every resource the graph still names," and
    `resource_index(bundle)` cannot answer that -- it indexes whatever is
    *currently on disk*, which trivially includes every stale page too.
    Using "on disk" as "should exist" would make deletion a permanent no-op.
    """

    written: tuple[str, ...] = field(default_factory=tuple)
    skipped: tuple[str, ...] = field(default_factory=tuple)
    current_resources: frozenset[str] = field(default_factory=frozenset)


#: `File.yaml`-style provenance keys that must never count as content drift
#: -- `generated.at` is a fresh wall-clock timestamp every call, so comparing
#: it would make every existing page look stale on every run. Mirrors
#: `mirror.plan._render_matches_disk`'s own `_PROVENANCE_KEYS` exclusion, for
#: the identical reason.
_PROVENANCE_KEYS = frozenset({"generated", "last_updated_commit", "tokens"})


@dataclass(frozen=True, slots=True)
class EntityPlan:
    """A read-only preview of what `sync_entities` would change, keyed by
    `resource` rather than `concept_id` -- `code_wiki_okf.sync`'s staleness
    rule reports against resources, the identity `SyncSnapshot` uses for
    both lanes.
    """

    stale: frozenset[str] = field(default_factory=frozenset)
    missing: frozenset[str] = field(default_factory=frozenset)
    current_resources: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class _Target:
    """One entity resolved to a page: where it lives, and what it renders to."""

    concept_id: str
    render: Render
    type_name: str
    title: str
    resource: str


def _repo_uri_by_name(reader: GraphReader) -> dict[str, str]:
    """`{repo short name: repo: URI}`, from every Repository node's own
    `.name` / `.attrs["uri"]` -- the `_repositories.yaml` template's own
    contract: the config key names both `repositories/<name>.md` and must
    match the graph's `repo:` URI member name."""
    out: dict[str, str] = {}
    for node in reader.list_repositories():
        uri = node.attrs.get("uri")
        if uri:
            out[node.name] = str(uri)
    return out


def _nodes_for_repo(nodes: Sequence[NodeRecord], repo_uri: str) -> list[NodeRecord]:
    """`nodes` attributed to `repo_uri` by code-graph-io's `repo` column
    (folded into `attrs["repo"]` by `queries._row_to_node`)."""
    return [n for n in nodes if n.attrs.get("repo") == repo_uri]


def _resource_text(node: NodeRecord) -> str:
    uri = node.attrs.get("uri")
    if not uri:
        raise ValueError(f"{node.kind} node {node.name!r} carries no uri -- cannot resolve a page resource for it")
    return str(uri)


def _resolve_target(
    *,
    existing: dict[str, str],
    schema_set: SchemaSet,
    type_name: str,
    name: str,
    resource: str,
    render: Render,
) -> _Target:
    """An existing page (found by `resource`) wins over a fresh path -- this
    is what lets a page moved within its lane be found and updated in place
    instead of duplicated."""
    concept_id = existing.get(resource) or default_concept_id(schema_set, type_name=type_name, name=name)
    return _Target(concept_id=concept_id, render=render, type_name=type_name, title=name, resource=resource)


def _stamp_provenance(render: Render, *, sha: str | None, at: datetime, tokens_source: str) -> Render:
    frontmatter = dict(render.frontmatter)
    frontmatter["generated"] = generated_value(by=f"code-wiki-okf/{__version__}", at=at)
    if sha:
        frontmatter["last_updated_commit"] = last_updated_commit_value(sha)
    frontmatter["tokens"] = tokens_value(count_tokens(tokens_source))
    return Render(frontmatter=frontmatter, sections=render.sections)


def _package_targets(
    nodes: Sequence[NodeRecord],
    reader: GraphReader,
    *,
    repo_name: str,
    existing: dict[str, str],
    schema_set: SchemaSet,
    sha: str | None,
    at: datetime,
) -> Iterator[_Target]:
    for node in nodes:
        desc = reader.describe_package(name=node.name)
        if desc is None:
            continue
        render = _stamp_provenance(
            render_package(desc, repo_name=repo_name), sha=sha, at=at, tokens_source="\n".join(desc.files)
        )
        yield _resolve_target(
            existing=existing,
            schema_set=schema_set,
            type_name="Package",
            name=node.name,
            resource=_resource_text(node),
            render=render,
        )


def _app_targets(
    nodes: Sequence[NodeRecord],
    reader: GraphReader,
    *,
    repo_name: str,
    existing: dict[str, str],
    schema_set: SchemaSet,
    sha: str | None,
    at: datetime,
) -> Iterator[_Target]:
    for node in nodes:
        desc = reader.describe_app(name=node.name)
        if desc is None:
            continue
        render = _stamp_provenance(
            render_app(desc, repo_name=repo_name), sha=sha, at=at, tokens_source="\n".join(desc.files)
        )
        yield _resolve_target(
            existing=existing,
            schema_set=schema_set,
            type_name="App",
            name=node.name,
            resource=_resource_text(node),
            render=render,
        )


def _test_suite_targets(
    nodes: Sequence[NodeRecord],
    reader: GraphReader,
    *,
    existing: dict[str, str],
    schema_set: SchemaSet,
    sha: str | None,
    at: datetime,
) -> Iterator[_Target]:
    for node in nodes:
        desc = reader.describe_test_suite(suite_name=node.name)
        if desc is None:
            continue
        tested = reader.consumer_packages(kind="test_suite", entity_uri=desc.uri)
        render = _stamp_provenance(
            render_test_suite(desc, tested_packages=tested), sha=sha, at=at, tokens_source="\n".join(tested)
        )
        yield _resolve_target(
            existing=existing,
            schema_set=schema_set,
            type_name="TestSuite",
            name=node.name,
            resource=_resource_text(node),
            render=render,
        )


def _agent_plugin_targets(
    nodes: Sequence[NodeRecord],
    reader: GraphReader,
    *,
    existing: dict[str, str],
    schema_set: SchemaSet,
    sha: str | None,
    at: datetime,
) -> Iterator[_Target]:
    for node in nodes:
        desc = reader.describe_agent_plugin(name=node.name)
        if desc is None:
            continue
        render = _stamp_provenance(render_agent_plugin(desc), sha=sha, at=at, tokens_source=desc.description)
        yield _resolve_target(
            existing=existing,
            schema_set=schema_set,
            type_name="AgentPlugin",
            name=node.name,
            resource=_resource_text(node),
            render=render,
        )


def _dependency_targets(
    nodes: Sequence[NodeRecord],
    reader: GraphReader,
    *,
    existing: dict[str, str],
    schema_set: SchemaSet,
    at: datetime,
) -> Iterator[_Target]:
    """Ecosystem-wide: no repo attribution and no commit SHA (a dependency
    is not owned by any one repo, so `last_updated_commit` never applies)."""
    for node in nodes:
        ecosystem = str(node.attrs.get("ecosystem", ""))
        desc = reader.describe_dependency(ecosystem=ecosystem, name=node.name)
        if desc is None:
            continue
        render = _stamp_provenance(render_dependency(desc), sha=None, at=at, tokens_source="\n".join(desc.used_by))
        yield _resolve_target(
            existing=existing,
            schema_set=schema_set,
            type_name="Dependency",
            name=node.name,
            resource=_resource_text(node),
            render=render,
        )


def _collision_message(collisions: dict[str, list[tuple[str, str]]]) -> str:
    parts = []
    for concept_id, entries in sorted(collisions.items()):
        named = ", ".join(f"{repo_label!r} ({resource})" for repo_label, resource in entries)
        parts.append(f"{concept_id}: {named}")
    return (
        "entity name collision -- these repos each declare an entity that would resolve to the same new page, "
        "and code-wiki-okf gives every repo one flat, ecosystem-wide namespace per lane (no per-repo "
        f"subdirectory): {'; '.join(parts)}. Rename one of the entities, or give it a distinct `resource:`."
    )


def _resolve_placements(
    bundle: Bundle,
    config: Config,
    reader: GraphReader,
    *,
    schema_set: SchemaSet,
    existing: dict[str, str],
    at: datetime,
) -> dict[str, tuple[_Target, str]]:
    """Resolve every entity this run's graph walk names to a page target,
    without writing anything. Shared by `sync_entities` (which writes what
    this returns) and `plan_entities` (which only previews it) -- extracted
    so the two can never compute a different "what should exist" answer.

    Raises `ValueError` for the same two cases `sync_entities` always has: a
    same-target collision between two repos' entities, and a new target whose
    default page path is already occupied by an unattributed file. Both
    checks are read-only themselves, so a preview caller gets them too.
    """
    repo_uris = _repo_uri_by_name(reader)
    all_packages = reader.list_packages()
    all_apps = reader.list_apps()
    all_suites = reader.list_test_suites()
    all_plugins = reader.list_agent_plugins()

    placed: dict[str, tuple[_Target, str]] = {}
    collisions: dict[str, list[tuple[str, str]]] = {}

    def place(target: _Target, *, repo_label: str) -> None:
        prior = placed.get(target.concept_id)
        if prior is not None and prior[0].resource != target.resource:
            bucket = collisions.setdefault(target.concept_id, [(prior[1], prior[0].resource)])
            bucket.append((repo_label, target.resource))
            return
        placed[target.concept_id] = (target, repo_label)

    for repo_cfg in config.repos:
        repo_uri = repo_uris.get(repo_cfg.name)
        if repo_uri is None:
            continue  # declared in _repositories.yaml but not (yet) present in the graph
        sha = head_commit(repo_cfg.path)

        for target in _package_targets(
            _nodes_for_repo(all_packages, repo_uri),
            reader,
            repo_name=repo_cfg.name,
            existing=existing,
            schema_set=schema_set,
            sha=sha,
            at=at,
        ):
            place(target, repo_label=repo_cfg.name)

        for target in _app_targets(
            _nodes_for_repo(all_apps, repo_uri),
            reader,
            repo_name=repo_cfg.name,
            existing=existing,
            schema_set=schema_set,
            sha=sha,
            at=at,
        ):
            place(target, repo_label=repo_cfg.name)

        for target in _test_suite_targets(
            _nodes_for_repo(all_suites, repo_uri),
            reader,
            existing=existing,
            schema_set=schema_set,
            sha=sha,
            at=at,
        ):
            place(target, repo_label=repo_cfg.name)

        for target in _agent_plugin_targets(
            _nodes_for_repo(all_plugins, repo_uri),
            reader,
            existing=existing,
            schema_set=schema_set,
            sha=sha,
            at=at,
        ):
            place(target, repo_label=repo_cfg.name)

        package_count = len(_nodes_for_repo(all_packages, repo_uri))
        repo_render = _stamp_provenance(
            render_repository(package_count=package_count), sha=sha, at=at, tokens_source=str(package_count)
        )
        place(
            _resolve_target(
                existing=existing,
                schema_set=schema_set,
                type_name="Repository",
                name=repo_cfg.name,
                resource=repo_uri,
                render=repo_render,
            ),
            repo_label=repo_cfg.name,
        )

    for target in _dependency_targets(
        reader.list_dependencies(), reader, existing=existing, schema_set=schema_set, at=at
    ):
        place(target, repo_label="(ecosystem-wide)")

    if collisions:
        raise ValueError(_collision_message(collisions))

    occupied = sorted(
        target.concept_id
        for target, _repo_label in placed.values()
        if target.resource not in existing and (bundle.root / f"{target.concept_id}.md").exists()
    )
    if occupied:
        raise ValueError(
            f"refusing to create: {', '.join(occupied)} -- a file already exists at each of these paths "
            f"with no matching `resource:` (missing, blank, or one okf-io could not coerce), so code-wiki-okf "
            f"cannot tell whether it is the same entity the graph now names. Give the existing page a "
            f"`resource:` matching the graph, or move it out of the way, before syncing again."
        )

    return placed


def sync_entities(
    bundle: Bundle,
    config: Config,
    reader: GraphReader,
    *,
    today: date,
    at: datetime,
) -> EntitySync:
    """Enumerate every Package/App/TestSuite/AgentPlugin/Dependency/Repository
    node `config` and `reader` can see, resolve each to an existing-or-new
    page, create the new ones on disk, then regenerate every target's owned
    frontmatter and generated sections in one `plan_regenerate` / `apply`
    pass.

    `today` carries no clock-dependent logic today; it is accepted so a
    future rule needing it does not need to widen every call site.

    Raises `ValueError` in the two cases `_resolve_placements` checks, both
    before any write lands: an entity-name collision between two repos, and a
    new page's default path already occupied by a file with no matching
    `resource:`.
    """
    schema_set = load_schemas(bundle.root / "_schema")
    section_set = load_sections(bundle.root / "_sections")
    existing: dict[str, str] = {
        resource: entry.concept_id for resource, entry in resource_index(bundle).by_resource.items()
    }

    placed = _resolve_placements(bundle, config, reader, schema_set=schema_set, existing=existing, at=at)

    # Phase 1: create every new page's skeleton on disk. A target whose
    # resource was already in the bundle -- whether at its default path or
    # one a human moved it to -- is left alone here; only its frontmatter and
    # generated sections change, in phase 2.
    created_any = False
    for target, _repo_label in placed.values():
        if target.resource in existing:
            continue
        text = new_page_text(
            schema_set=schema_set,
            section_set=section_set,
            type_name=target.type_name,
            title=target.title,
            resource=target.resource,
        )
        page_path = bundle.root / f"{target.concept_id}.md"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(text, encoding="utf-8")
        created_any = True

    # Phase 2: reload so new pages are bundle members, then regenerate only
    # targets whose CONTENT changed (owned keys + sections, excluding
    # provenance) plus every freshly-created page this run. Provenance keys
    # are declared alongside `owned` ones in each type's `_sections/<Type>.yaml`
    # and get rewritten by `plan_regenerate` whenever ANY of a render's
    # frontmatter differs from disk -- `generated.at` is a fresh wall-clock
    # timestamp on every real invocation, so unconditionally handing every
    # target's full render to `plan_regenerate` would regenerate every page on
    # every run regardless of whether anything actually changed. This mirrors
    # `mirror.plan._render_matches_disk`'s existing content-only pre-filter,
    # and reuses the exact comparison `plan_entities` already performs for its
    # own read-only preview -- entity sync just never got the same treatment
    # on its real write path.
    working_bundle = load_bundle(bundle.root) if created_any else bundle

    content_plan = plan_regenerate(
        working_bundle,
        section_set,
        {
            target.concept_id: Render(
                frontmatter={k: v for k, v in target.render.frontmatter.items() if k not in _PROVENANCE_KEYS},
                sections=target.render.sections,
            )
            for target, _repo_label in placed.values()
        },
    )
    stale_or_new = frozenset(content_plan.concept_ids) | {
        target.concept_id for target, _repo_label in placed.values() if target.resource not in existing
    }

    renders = {
        target.concept_id: target.render for target, _repo_label in placed.values() if target.concept_id in stale_or_new
    }
    plan = plan_regenerate(working_bundle, section_set, renders)
    result = apply_regenerations(working_bundle, plan)

    written = tuple(member.removesuffix(".md") for member in result.written)
    skipped = tuple(f"{item.path}: {item.reason}" for item in result.skipped)
    current_resources = frozenset(target.resource for target, _repo_label in placed.values())
    return EntitySync(written=written, skipped=skipped, current_resources=current_resources)


def plan_entities(bundle: Bundle, config: Config, reader: GraphReader, *, at: datetime) -> EntityPlan:
    """Preview `sync_entities` without writing anything.

    Resolves the same placements `sync_entities` would (`_resolve_placements`,
    shared), then asks `plan_regenerate` -- itself a read-only preview, per
    its own docstring -- what a content-only version of those renders would
    change. A target `plan_regenerate` cannot find as a bundle member
    surfaces as `Skipped(reason="unreadable")`; that is exactly "no page yet",
    so `missing` reads off it directly rather than re-deriving existence a
    second way.
    """
    schema_set = load_schemas(bundle.root / "_schema")
    section_set = load_sections(bundle.root / "_sections")
    existing: dict[str, str] = {
        resource: entry.concept_id for resource, entry in resource_index(bundle).by_resource.items()
    }
    placed = _resolve_placements(bundle, config, reader, schema_set=schema_set, existing=existing, at=at)

    renders: dict[str, Render] = {}
    resource_by_concept: dict[str, str] = {}
    for target, _repo_label in placed.values():
        content_only = Render(
            frontmatter={k: v for k, v in target.render.frontmatter.items() if k not in _PROVENANCE_KEYS},
            sections=target.render.sections,
        )
        renders[target.concept_id] = content_only
        resource_by_concept[target.concept_id] = target.resource

    plan = plan_regenerate(bundle, section_set, renders)
    stale = frozenset(resource_by_concept[concept_id] for concept_id in plan.concept_ids)
    missing = frozenset(
        resource_by_concept[skipped.concept_id]
        for skipped in plan.skipped
        if skipped.reason == "unreadable" and skipped.concept_id in resource_by_concept
    )
    current_resources = frozenset(target.resource for target, _repo_label in placed.values())
    return EntityPlan(stale=stale, missing=missing, current_resources=current_resources)


__all__ = ["EntityPlan", "EntitySync", "plan_entities", "sync_entities"]

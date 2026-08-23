"""Build the read-only staleness/missing/orphan sets reported by sync rules.

Every call this makes is a preview through the same entity and mirror
planners the composite writer consumes. ``snapshot_bundle`` never touches
disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from code_graph_io.handle import GraphReader
from okf_ext.bundle import SECTIONS_DIRNAME
from okf_ext.generators import plan_regenerate
from okf_ext.shape import load_sections
from okf_io import Bundle

from code_wiki_okf.config import Config
from code_wiki_okf.entities.sync import _render_for_apply, plan_entities
from code_wiki_okf.git_state import head_commit
from code_wiki_okf.mirror.plan import plan_mirror
from code_wiki_okf.mirror.walk import tracked_files
from code_wiki_okf.placement import (
    PlacementError,
    canonical_concept_id,
    context_from_resource,
    is_code_wiki_type,
)


def _existing_resources_by_lane(bundle: Bundle) -> tuple[frozenset[str], frozenset[str]]:
    """Canonical owned resources already backing entity and File pages.

    Ownership comes from the declared code-wiki type. Exact placement is
    validated through the same policy the writers use; path shape and the
    configured repository short names are not classifiers.
    """
    entity_resources: set[str] = set()
    file_resources: set[str] = set()
    for concept_id, document in bundle.concepts.items():
        if document.parse_error is not None:
            continue
        type_name = document.fm.type or ""
        if not is_code_wiki_type(type_name):
            continue
        resource = document.fm.resource
        if resource is None:
            continue
        context = context_from_resource(type_name, resource)
        expected = canonical_concept_id(context)
        if concept_id != expected:
            raise PlacementError(
                resource=resource,
                reason=f"found at {concept_id}.md; delete the old pre-release page and regenerate",
                expected=expected,
            )
        if type_name == "File":
            file_resources.add(resource)
        else:
            entity_resources.add(resource)
    return frozenset(entity_resources), frozenset(file_resources)


@dataclass(frozen=True, slots=True)
class SyncSnapshot:
    """Three resource sets a `RuleContext`-only rule cannot derive itself --
    `RuleContext` carries `bundle`/`links`/`today`, nothing that could answer
    "did the source change" or "does this file still exist". Plain
    `frozenset[str]` rather than a richer structure: the rule only needs
    "is this page's resource a member"; why it's stale belongs in the log,
    not a one-line `Finding.message`.
    """

    stale: frozenset[str] = field(default_factory=frozenset)
    missing: frozenset[str] = field(default_factory=frozenset)
    orphaned: frozenset[str] = field(default_factory=frozenset)

    @staticmethod
    def empty() -> SyncSnapshot:
        return SyncSnapshot()


def snapshot_bundle(bundle: Bundle, config: Config, reader: GraphReader, *, at: datetime) -> SyncSnapshot:
    """Read-only across both lanes. See module docstring."""
    entity_plan = plan_entities(bundle, reader, config, at=at.isoformat())
    existing_entity_resources, existing_file_resources = _existing_resources_by_lane(bundle)
    missing: set[str] = set(entity_plan.current_resources - existing_entity_resources)
    orphaned: set[str] = set(existing_entity_resources - entity_plan.current_resources) | set(existing_file_resources)

    entity_renders = {
        write.member.removesuffix(".md"): _render_for_apply(write, content_only=True)
        for write in entity_plan.writes
        if write.context.resource in existing_entity_resources
    }
    entity_regeneration = plan_regenerate(
        bundle,
        load_sections(config.declarations_dir / SECTIONS_DIRNAME),
        entity_renders,
    )
    stale: set[str] = {
        resource
        for concept_id in entity_regeneration.concept_ids
        if (resource := bundle.concepts[concept_id].fm.resource) is not None
    }

    walked = tracked_files(config)
    for repo in config.repos:
        sha = head_commit(repo.path)
        if sha is None:
            continue  # not a git checkout -- git_state's own "can't answer" contract
        plan = plan_mirror(bundle, reader, repo, tracked=walked.get(repo.name, ()), sha=sha, at=at)
        # The ownership pass above deliberately knows nothing about config.
        # A configured repo's plan can now clear every File page it recognized
        # as belonging to that repo, before adding genuinely vanished sources
        # back below. Files for a removed repo never acquire such a plan and
        # consequently remain orphaned.
        orphaned.difference_update(target.resource for target in plan.targets)
        missing |= {plan.target_for(rel_path).resource for rel_path in plan.creates}
        stale |= {
            resource for concept_id in plan.updates if (resource := bundle.concepts[concept_id].fm.resource) is not None
        }
        orphaned |= {plan.target_for(rel_path).resource for rel_path in plan.deletions}
        orphaned |= {declined.resource for declined in plan.declined_deletions}

    return SyncSnapshot(stale=frozenset(stale), missing=frozenset(missing), orphaned=frozenset(orphaned))


__all__ = ["SyncSnapshot", "snapshot_bundle"]

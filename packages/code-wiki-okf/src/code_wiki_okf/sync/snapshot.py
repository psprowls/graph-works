"""Build the read-only staleness/missing/orphan sets `sync_rule` reports
against. Every call this makes is a preview -- `entities.sync.plan_entities`
and `mirror.plan.plan_mirror` -- never `sync_entities`, `entities.lanes.sync`,
or `mirror.apply.apply_mirror`, all of which write. `snapshot_bundle` never
touches disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from code_graph_io.handle import GraphReader
from okf_io import Bundle

from code_wiki_okf.config import Config
from code_wiki_okf.entities.lanes import ENTITY_LANES
from code_wiki_okf.entities.sync import plan_entities
from code_wiki_okf.git_state import head_commit
from code_wiki_okf.mirror.plan import plan_mirror
from code_wiki_okf.mirror.walk import tracked_files
from code_wiki_okf.resources import resource_index

#: The entity lanes with no nested-subtree ambiguity -- a plain prefix match
#: is safe for these. Derived from `entities.lanes.ENTITY_LANES` rather than
#: hand-duplicated, so a lane added there can never silently fall out of sync
#: with what this module treats as "an existing entity page" -- the failure
#: mode of a hand-copied tuple would be `.orphaned` quietly miscounting that
#: new lane's pages forever, not an error. `repositories/` is excluded here:
#: it prefixes both the Repository entity page (`repositories/<name>`) and
#: every mirror File page under it (`repositories/<name>/<rel_path>`), so it
#: needs the depth-aware check in `_is_entity_repository_page` instead.
_ENTITY_LANE_PREFIXES: tuple[str, ...] = tuple(lane for lane in ENTITY_LANES if lane != "repositories/")


def _is_entity_repository_page(concept_id: str) -> bool:
    """`repositories/<name>` (the Repository entity page) -- never
    `repositories/<name>/<rel_path...>` (a mirror File page). A plain
    `startswith("repositories/")` cannot tell the two apart; `entities/delete.py`'s
    own docstring warns callers to keep the distinction, which this makes by
    checking depth instead of prefix alone.
    """
    rest = concept_id.removeprefix("repositories/")
    return rest != concept_id and "/" not in rest


def _existing_entity_resources(bundle: Bundle) -> frozenset[str]:
    """Every resource already backing a page in one of the entity lanes.

    Generalizes `prune_lane`'s per-lane, per-`exact_depth` filtering
    (`entities/delete.py`) into one combined check across all lanes at once:
    `prune_lane` runs once per lane and only ever needs that lane's own
    resources, but this needs "is this resource *any* entity lane's page" in
    a single pass, so a naive `resource_index(bundle)` filter (which carries
    every resource in the bundle, mirror File pages included) would wrongly
    fold mirror pages in without the same prefix/depth discipline.
    """
    resources: set[str] = set()
    for resource, entry in resource_index(bundle).by_resource.items():
        concept_id = entry.concept_id
        if concept_id.startswith(_ENTITY_LANE_PREFIXES) or _is_entity_repository_page(concept_id):
            resources.add(resource)
    return frozenset(resources)


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
    entity_plan = plan_entities(bundle, config, reader, at=at)
    stale: set[str] = set(entity_plan.stale)
    missing: set[str] = set(entity_plan.missing)
    orphaned: set[str] = set(_existing_entity_resources(bundle) - entity_plan.current_resources)

    walked = tracked_files(config)
    for repo in config.repos:
        sha = head_commit(repo.path)
        if sha is None:
            continue  # not a git checkout -- git_state's own "can't answer" contract
        plan = plan_mirror(bundle, reader, repo, tracked=walked.get(repo.name, ()), sha=sha, at=at)
        prefix = f"file:{repo.name}/"
        missing |= {f"{prefix}{rel_path}" for rel_path in plan.creates}
        stale |= {
            resource for concept_id in plan.updates if (resource := bundle.concepts[concept_id].fm.resource) is not None
        }
        orphaned |= {f"{prefix}{rel_path}" for rel_path in plan.deletions}
        orphaned |= {declined.resource for declined in plan.declined_deletions}

    return SyncSnapshot(stale=frozenset(stale), missing=frozenset(missing), orphaned=frozenset(orphaned))


__all__ = ["SyncSnapshot", "snapshot_bundle"]

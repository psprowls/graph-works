"""Classify one repo's tracked-file set against its existing File pages.

The engine: current tracked set vs. previous File-page set (found by
`resource`), the difference resolved into creates / deletions, then git's
own rename detection (`git_state.find_renames`) reclassifying matched
vanished/new pairs into moves before anything else is decided, then the
deletion guard over what is left.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from code_graph_io.handle import GraphReader
from okf_ext.generators import Render
from okf_ext.moves import plan_move_many
from okf_io import Bundle, Document

from code_wiki_okf.config import RepoConfig
from code_wiki_okf.git_state import earliest_common_ancestor, find_renames
from code_wiki_okf.mirror.model import DeclinedDeletion, MirrorPlan, MirrorTarget
from code_wiki_okf.mirror.render import render_file
from code_wiki_okf.placement import (
    PlacementContext,
    PlacementError,
    canonical_concept_id,
    canonical_member,
    context_from_resource,
    filesystem_member_identity,
)
from code_wiki_okf.resources import ResourceIndex, resource_index

_NOTES_PLACEHOLDER = (
    "> TODO: anything a reader should know about this file that the generated sections below don't capture."
)

# `File.yaml`'s declared `provenance:` keys -- always freshly computed
# (`generated.at` is a fresh wall-clock timestamp every sync run, per
# `render_file`'s caller), so they can never equal what is already on disk
# even when nothing about the file's *content* changed. Excluded from
# `_render_matches_disk`'s comparison for exactly that reason: comparing them
# would make every rich page look "different" every single run, defeating the
# idempotence check this function exists for. Hardcoded rather than read off
# a loaded `SectionSet` -- this module takes no such dependency (see
# `_render_matches_disk`'s own docstring).
_PROVENANCE_KEYS = frozenset({"generated", "last_updated_commit", "tokens"})


def _heading_body(body: str, heading: str) -> str | None:
    """The stripped content of `## <heading>`'s section, or `None` if the
    body carries no such heading. Generalizes what `_notes_matches_placeholder`
    used to inline for `Notes` alone, so the same extraction now also backs
    the update-candidate idempotence check below.
    """
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(body)
    if match is None:
        return None
    rest = body[match.end() :]
    next_heading = re.search(r"^##\s+\S", rest, re.MULTILINE)
    section_body = rest[: next_heading.start()] if next_heading else rest
    return section_body.strip()


def _notes_matches_placeholder(body: str) -> bool:
    """Whether the `## Notes` section's content still equals its placeholder."""
    return _heading_body(body, "Notes") == _NOTES_PLACEHOLDER.strip()


def _render_matches_disk(document: Document, render: Render) -> bool:
    """Whether *render* would change nothing **content-bearing** about
    *document* -- every *owned* frontmatter key it carries already equals
    what's on disk, and every generated section's body already equals too.

    `render.frontmatter` also carries `File.yaml`'s `provenance` keys
    `generated` and `last_updated_commit` alongside the owned ones -- see
    `render_file`'s docstring. Provenance is *always* freshly computed
    (`generated.at` is a new wall-clock timestamp on every real sync run), so
    it can never equal what's already on disk even when the file's content
    hasn't changed at all. Comparing it here would make `_render_matches_disk`
    return `False` for every rich page on every run, in production, even
    though the underlying content is unchanged -- an idempotence check that
    always fails is not one. `_PROVENANCE_KEYS` also carries `tokens`, though
    `render_file` no longer stamps it: `run_tokens_update` (graph-works-core)
    is that key's sole writer now, so a *foreign* process changes it between
    runs, and comparing it here would make every page look stale on the first
    `gw util tokens` after a scan -- the same failure mode, a different cause.
    The exclusion is so this only ever compares what a human or the graph
    could actually have changed: `owned` keys and section bodies. A page
    recognized as unchanged this way is never proposed as an update, so its
    provenance stamp is correctly left alone rather than silently refreshed.

    The full write-time idempotence check lives in
    `okf_ext.generators.plan_regenerate` (declaration-aware, section-splice-
    aware); this is the same idea, done with only what a `MirrorPlan` already
    has on hand -- no `SectionSet` dependency added to this module's
    signature for it.
    """
    for key, value in render.frontmatter.items():
        if key in _PROVENANCE_KEYS:
            continue
        if document.fm_raw.get(key) != value:
            return False
    return all(_heading_body(document.body, heading) == content.strip() for heading, content in render.sections.items())


def _repository_resource(reader: GraphReader, repo: RepoConfig) -> str:
    resources = tuple(
        sorted(
            str(uri) for node in reader.list_repositories() if node.name == repo.name and (uri := node.attrs.get("uri"))
        )
    )
    if not resources:
        raise PlacementError(
            resource=f"repo:{repo.name}",
            reason=f"no graph Repository resource matches configured repository {repo.name!r}",
        )
    if len(resources) > 1:
        raise PlacementError(
            resource=resources[0],
            reason=f"repository name {repo.name!r} is claimed by {', '.join(resources)}",
        )
    return resources[0]


def _target(context: PlacementContext) -> MirrorTarget:
    member = canonical_member(context)
    source_path = context.source_path
    if source_path is None:
        raise PlacementError(resource=context.resource, reason="File placement context has no source path")
    if PurePosixPath(member).name == "index.md":
        raise PlacementError(
            resource=context.resource,
            reason="File page would collide with a reserved directory index.md",
            expected=canonical_concept_id(context),
        )
    return MirrorTarget(resource=context.resource, source_path=source_path, member=member)


def _target_from_source(repository_resource: str, source_path: str) -> MirrorTarget:
    payload = repository_resource.removeprefix("repo:")
    resource = f"file:{payload}/{source_path}"
    return _target(context_from_resource("File", resource))


def plan_mirror(
    bundle: Bundle,
    reader: GraphReader,
    repo: RepoConfig,
    *,
    tracked: tuple[str, ...],
    sha: str,
    at: datetime,
    index: ResourceIndex | None = None,
) -> MirrorPlan:
    """Plan this repo's mirror sync: creates, rich-page updates, renames, deletions."""
    repository_resource = _repository_resource(reader, repo)
    repository_context = context_from_resource("Repository", repository_resource)
    repository_name = repository_context.repository
    if repository_name is None:
        raise PlacementError(resource=repository_resource, reason="Repository placement context has no repository")

    desired = {
        source_path: _target_from_source(repository_resource, source_path) for source_path in sorted(set(tracked))
    }
    index = resource_index(bundle) if index is None else index
    resource_prefix = f"file:{repository_resource.removeprefix('repo:')}/"
    for resource, members in index.members_by_resource.items():
        if resource.startswith(resource_prefix) and len(members) > 1:
            raise PlacementError(
                resource=resource,
                reason=f"duplicate resource in {', '.join(sorted(members))}; delete duplicate pages and regenerate",
            )

    previous: dict[str, MirrorTarget] = {}
    for resource, entry in index.by_resource.items():
        if not resource.startswith(resource_prefix):
            continue
        target = _target(context_from_resource("File", resource))
        actual_member = f"{entry.concept_id}.md"
        if actual_member != target.member:
            raise PlacementError(
                resource=resource,
                reason=f"found at {actual_member}; delete the old pre-release page and regenerate",
                expected=target.member.removesuffix(".md"),
            )
        actual_type = entry.document.fm.type or ""
        if actual_type != "File":
            raise PlacementError(
                resource=resource,
                reason=f"{actual_member} declares type {actual_type or '(blank)'} instead of File",
                expected=target.member.removesuffix(".md"),
            )
        previous[target.source_path] = target

    resource_by_member: dict[str, tuple[str, str]] = {}
    for target in desired.values():
        identity = filesystem_member_identity(target.member)
        prior = resource_by_member.get(identity)
        if prior is not None and prior[1] != target.resource:
            raise PlacementError(
                resource=target.resource,
                reason=(
                    f"canonical member {target.member} is filesystem-equivalent to {prior[0]}, "
                    f"which also belongs to {prior[1]}"
                ),
                expected=target.member.removesuffix(".md"),
            )
        resource_by_member[identity] = (target.member, target.resource)

        path_conflicts = index.filesystem_path_conflicts_for(target.member)
        if path_conflicts:
            raise PlacementError(
                resource=target.resource,
                reason=(
                    f"canonical member {target.member} has a filesystem-equivalent path type conflict with existing "
                    f"{', '.join(path_conflicts)}"
                ),
                expected=target.member.removesuffix(".md"),
            )
        existing = bundle.concept(target.member.removesuffix(".md"))
        if existing is not None and existing.fm.resource != target.resource:
            raise PlacementError(
                resource=target.resource,
                reason=f"found an existing page at {target.member} with no matching resource",
                expected=target.member.removesuffix(".md"),
            )
        equivalent_members = index.filesystem_members_for(target.member)
        if equivalent_members and equivalent_members != (target.member,):
            raise PlacementError(
                resource=target.resource,
                reason=(
                    f"canonical member {target.member} is filesystem-equivalent to existing "
                    f"{', '.join(equivalent_members)}"
                ),
                expected=target.member.removesuffix(".md"),
            )
        if existing is None and (bundle.root / target.member).exists():
            raise PlacementError(
                resource=target.resource,
                reason=f"found an existing page at {target.member} with no matching resource",
                expected=target.member.removesuffix(".md"),
            )

    tracked_set = set(desired)
    previous_set = set(previous)
    candidate_creates = tracked_set - previous_set
    candidate_deletions = previous_set - tracked_set

    # --- rename detection: reclassify matched vanished/new pairs first ---
    # Scoped to the candidate-deletion set's own `last_updated_commit`
    # values only -- an unrelated page elsewhere in the bundle with an older
    # commit must not widen the diff window.
    commits: list[str] = []
    for rel_path in candidate_deletions:
        document = bundle.concepts[previous[rel_path].member.removesuffix(".md")]
        commit = document.fm.extra.get("last_updated_commit")
        if isinstance(commit, str) and commit:
            commits.append(commit)

    rename_map: dict[str, str] = {}
    if commits:
        # The diff base must be a commit that predates every one of these
        # stamps -- git's own ancestry answers that, a string `min()` over
        # SHA hex does not (SHA-1 has no relation to commit order, so the
        # lexicographically smallest stamp can easily be the *most recent*
        # one, picking a base that starts the diff window after a rename
        # already happened and silently missing it).
        base = earliest_common_ancestor(repo.path, commits)
        detected = find_renames(repo.path, base, head=sha) if base is not None else None
        for old_path, new_path in detected or []:
            if old_path in candidate_deletions and new_path in candidate_creates:
                rename_map[old_path] = new_path

    candidate_creates -= set(rename_map.values())
    candidate_deletions -= set(rename_map.keys())

    # Both sides are the canonical members preflighted above. A page found at
    # any other member has already refused planning rather than being moved as
    # an implicit migration.
    moves_mapping = {previous[old].member: desired[new].member for old, new in rename_map.items()}
    moves_plan = plan_move_many(bundle, moves_mapping)

    # --- creates ---
    creates: dict[str, tuple[dict[str, Any], Render]] = {}
    for rel_path in sorted(candidate_creates):
        creates[rel_path] = render_file(
            reader,
            context_from_resource("File", desired[rel_path].resource),
            at=at,
            sha=sha,
        )

    # --- updates: rich pages already present, present in both sets ---
    # Keyed by the page's *actual* concept_id from the resource index, not a
    # reconstruction of the conventional mirrored path -- pages are found by
    # `resource`, never by expected path (see `resources.py`).
    updates: dict[str, Render] = {}
    for rel_path in sorted(tracked_set & previous_set):
        _frontmatter, render = render_file(
            reader,
            context_from_resource("File", desired[rel_path].resource),
            at=at,
            sha=sha,
        )
        if not render.sections:  # rich only -- a bare Render() against a declared type deletes owned keys
            continue
        concept_id = previous[rel_path].member.removesuffix(".md")
        if _render_matches_disk(bundle.concepts[concept_id], render):
            continue  # already on disk -- proposing it again is not idempotent
        updates[concept_id] = render

    # --- deletion guard over what remains unpaired ---
    deletions: list[str] = []
    declined: list[DeclinedDeletion] = []
    for rel_path in sorted(candidate_deletions):
        concept_id = previous[rel_path].member.removesuffix(".md")
        document = bundle.concepts[concept_id]
        if _notes_matches_placeholder(document.body):
            deletions.append(rel_path)
        else:
            declined.append(
                DeclinedDeletion(
                    resource=document.fm.resource or "",
                    path=f"{concept_id}.md",
                    reason="prose-edited",
                )
            )

    return MirrorPlan(
        repo=repository_name,
        targets=tuple(sorted({*desired.values(), *previous.values()}, key=lambda target: target.source_path)),
        moves=moves_plan,
        creates=creates,
        updates=updates,
        deletions=tuple(deletions),
        declined_deletions=tuple(declined),
    )


__all__ = ["plan_mirror"]

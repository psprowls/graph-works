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
from typing import Any

from code_graph_io.handle import GraphReader
from okf_ext.generators import Render
from okf_ext.moves import plan_move_many
from okf_io import Bundle, Document

from code_wiki_okf.config import RepoConfig
from code_wiki_okf.git_state import earliest_common_ancestor, find_renames
from code_wiki_okf.mirror.model import DeclinedDeletion, MirrorPlan
from code_wiki_okf.mirror.render import render_file
from code_wiki_okf.resources import resource_index

_NOTES_PLACEHOLDER = (
    "> TODO: <Anything a reader should know about this file that the generated sections below don't capture.>"
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


def _mirror_prefix(repo_name: str) -> str:
    return f"repositories/{repo_name}"


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
    (`generated`, `last_updated_commit`, `tokens`) alongside the owned ones --
    see `render_file`'s docstring. Provenance is *always* freshly computed
    (`generated.at` is a new wall-clock timestamp on every real sync run), so
    it can never equal what's already on disk even when the file's content
    hasn't changed at all. Comparing it here would make `_render_matches_disk`
    return `False` for every rich page on every run, in production, even
    though the underlying content is unchanged -- an idempotence check that
    always fails is not one. `_PROVENANCE_KEYS` is excluded so this only ever
    compares what a human or the graph could actually have changed: `owned`
    keys and section bodies. A page recognized as unchanged this way is never
    proposed as an update, so its provenance stamp is correctly left alone
    rather than silently refreshed.

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


def plan_mirror(
    bundle: Bundle,
    reader: GraphReader,
    repo: RepoConfig,
    *,
    tracked: tuple[str, ...],
    sha: str,
    at: datetime,
) -> MirrorPlan:
    """Plan this repo's mirror sync: creates, rich-page updates, renames, deletions."""
    prefix = _mirror_prefix(repo.name)
    index = resource_index(bundle)
    previous: dict[str, str] = {}  # rel_path -> concept_id, for this repo's mirror only
    resource_prefix = f"file:{repo.name}/"
    for resource, entry in index.by_resource.items():
        if resource.startswith(resource_prefix):
            previous[resource[len(resource_prefix) :]] = entry.concept_id

    tracked_set = set(tracked)
    previous_set = set(previous)
    candidate_creates = tracked_set - previous_set
    candidate_deletions = previous_set - tracked_set

    # --- rename detection: reclassify matched vanished/new pairs first ---
    # Scoped to the candidate-deletion set's own `last_updated_commit`
    # values only -- an unrelated page elsewhere in the bundle with an older
    # commit must not widen the diff window.
    commits: list[str] = []
    for rel_path in candidate_deletions:
        document = bundle.concepts[previous[rel_path]]
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

    # `dest` is the conventional mirrored path -- exactly where a fresh
    # create would land, since nothing lives there yet. `source` is the
    # page's *actual* concept_id from the resource index, not a
    # reconstruction of the conventional path from `old` -- a page found by
    # resource may not live at the path its resource would conventionally
    # imply, and `plan_move_many` would refuse a source that is not truly a
    # bundle member.
    moves_mapping = {f"{previous[old]}.md": f"{prefix}/{new}.md" for old, new in rename_map.items()}
    moves_plan = plan_move_many(bundle, moves_mapping)

    # --- creates ---
    creates: dict[str, tuple[dict[str, Any], Render]] = {}
    for rel_path in sorted(candidate_creates):
        creates[rel_path] = render_file(reader, repo, rel_path, at=at, sha=sha)

    # --- updates: rich pages already present, present in both sets ---
    # Keyed by the page's *actual* concept_id from the resource index, not a
    # reconstruction of the conventional mirrored path -- pages are found by
    # `resource`, never by expected path (see `resources.py`).
    updates: dict[str, Render] = {}
    for rel_path in sorted(tracked_set & previous_set):
        _frontmatter, render = render_file(reader, repo, rel_path, at=at, sha=sha)
        if not render.sections:  # rich only -- a bare Render() against a declared type deletes owned keys
            continue
        concept_id = previous[rel_path]
        if _render_matches_disk(bundle.concepts[concept_id], render):
            continue  # already on disk -- proposing it again is not idempotent
        updates[concept_id] = render

    # --- deletion guard over what remains unpaired ---
    deletions: list[str] = []
    declined: list[DeclinedDeletion] = []
    for rel_path in sorted(candidate_deletions):
        concept_id = previous[rel_path]
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
        repo=repo.name,
        moves=moves_plan,
        creates=creates,
        updates=updates,
        deletions=tuple(deletions),
        declined_deletions=tuple(declined),
    )


__all__ = ["plan_mirror"]

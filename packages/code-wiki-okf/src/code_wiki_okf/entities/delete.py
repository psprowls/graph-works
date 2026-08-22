"""Deletion is reconciliation with a prose guard (epic, "Drift, staleness,
deletion"). A page whose resource is no longer in the graph is deleted
outright ONLY if every one of its declared `prose`-ownership sections still
equals that section's seeded placeholder. `generated`/`template` sections
are never checked -- they are never human-authored.

The comparison logic mirrors `okf_ext.sections.rule._normalized` (line
endings normalised, each line stripped, leading/trailing blank lines
dropped) but is re-implemented here rather than imported: that function is
private to its module, and this check additionally covers **optional**
prose sections (`sections.unfilled` only ever considers `required` ones --
see that rule's own `if not spec.required or spec.seeded_is_complete:
continue` guard -- so it cannot be reused as-is for a check that must also
protect an untouched optional section like Package's `## Public API`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from okf_ext.body import find_section
from okf_ext.shape import SectionSet
from okf_io import Bundle

from code_wiki_okf.resources import resource_index


@dataclass(frozen=True, slots=True)
class PruneResult:
    deleted: tuple[str, ...] = field(default_factory=tuple)
    declined: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (concept_id, reason)


def _normalized(text: str) -> str:
    flat = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in flat.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _decline_reason(body: str, section_set: SectionSet, type_name: str) -> str | None:
    """`None` means safe to delete. Anything else is why not.

    A type with no matching declaration in *section_set* -- blank type,
    misspelled type, or a type simply absent from the currently-loaded
    `sections/` directory, all real possibilities under okf-io's
    tolerant/never-raise content model -- declines rather than deletes.
    There is no declaration to check prose against, and the page whose type
    we cannot even resolve is exactly the one to be most cautious about, not
    least: an absent declaration must never read as "nothing to guard."
    """
    declaration = section_set.types.get(type_name)
    if declaration is None:
        return "no-declaration-for-type"
    for spec in declaration.sections:
        if spec.ownership != "prose":
            continue
        found = find_section(body, spec.heading, level=spec.level)
        if found is None:
            continue  # nothing added by a human -- not a reason to decline
        content = _normalized(found.slice(body))
        if content and content != _normalized(spec.placeholder):
            return "prose-edited"
    return None


def prune_lane(
    bundle: Bundle,
    section_set: SectionSet,
    *,
    directory: str,
    should_exist: set[str],
    exact_depth: bool = False,
) -> PruneResult:
    """*directory* is a bundle-relative prefix (`"packages/"`) -- the entity
    lane only; the mirror lane's own subdirectory under `repositories/<name>/`
    is child 3's business and is never touched here.

    `exact_depth=True` additionally requires no further `/` after *directory*
    in the concept id. A plain prefix match on `"repositories/"` also catches
    a repo's own mirror subtree (`repositories/<name>/<rel_path>`) -- this
    was documented as a caveat here but not actually enforced, and combining
    the entity and mirror lanes in one `sync` run surfaced it as a real bug:
    the entity lane would delete mirror File pages it has no business
    touching. Callers pass `exact_depth=True` for the `repositories/` lane
    specifically; the other five lanes have no such nested subtree and don't
    need it.
    """
    index = resource_index(bundle)
    deleted: list[str] = []
    declined: list[tuple[str, str]] = []

    for resource, entry in sorted(index.by_resource.items()):
        if not entry.concept_id.startswith(directory):
            continue
        if exact_depth and "/" in entry.concept_id.removeprefix(directory):
            continue
        if resource in should_exist:
            continue
        document = entry.document
        type_name = (document.fm.type or "").strip()
        reason = _decline_reason(document.body, section_set, type_name)
        if reason is None:
            path = bundle.root / f"{entry.concept_id}.md"
            path.unlink()
            deleted.append(entry.concept_id)
        else:
            declined.append((entry.concept_id, reason))

    return PruneResult(deleted=tuple(deleted), declined=tuple(declined))

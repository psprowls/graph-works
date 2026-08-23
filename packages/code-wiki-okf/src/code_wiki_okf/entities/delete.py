"""Policy-typed deletion with provenance and prose guards.

A stale page is considered only when its declared type belongs to the
code-wiki placement vocabulary. It is deleted only when code-wiki provenance
claims it and every declared ``prose`` section still equals its seeded
placeholder. ``generated``/``template`` sections are never checked because
they are not human-authored.

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

from collections.abc import Mapping, Set
from dataclasses import dataclass, field
from pathlib import Path

from okf_ext.body import find_section
from okf_ext.bundle import SECTIONS_DIRNAME
from okf_ext.shape import SectionSet, load_sections
from okf_io import Bundle

from code_wiki_okf.placement import is_code_wiki_type


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


def _is_generated(document_frontmatter: Mapping[str, object]) -> bool:
    generated = document_frontmatter.get("generated")
    if not isinstance(generated, Mapping):
        return False
    actor = generated.get("by")
    return isinstance(actor, str) and (actor == "code-wiki-okf" or actor.startswith("code-wiki-okf/"))


def plan_prune_entities(
    bundle: Bundle,
    current_resources: Set[str],
    *,
    declarations_dir: Path | None = None,
) -> PruneResult:
    """Classify stale pages without changing the bundle.

    Directory names and depths are deliberately irrelevant: a misplaced
    generated page is still ours to remove, while an unrelated human concept
    beneath a generated directory is not.  Generated provenance and every
    declared prose section must both remain untouched before deletion is
    allowed.
    """
    declarations_root = bundle.root if declarations_dir is None else declarations_dir
    section_set = load_sections(declarations_root / SECTIONS_DIRNAME)
    deleted: list[str] = []
    declined: list[tuple[str, str]] = []

    for concept_id, document in sorted(bundle.concepts.items()):
        type_name = (document.fm.type or "").strip()
        resource = document.fm.resource
        if not is_code_wiki_type(type_name) or resource is None:
            continue
        if resource in current_resources:
            continue
        reason = _decline_reason(document.body, section_set, type_name)
        if reason is None and not _is_generated(document.fm_raw):
            reason = "not-generated"
        if reason is None:
            deleted.append(concept_id)
        else:
            declined.append((concept_id, reason))

    return PruneResult(deleted=tuple(deleted), declined=tuple(declined))


def prune_entities(
    bundle: Bundle,
    current_resources: Set[str],
    *,
    declarations_dir: Path | None = None,
) -> PruneResult:
    """Apply the exact guarded deletion classification for this bundle."""
    plan = plan_prune_entities(bundle, current_resources, declarations_dir=declarations_dir)
    for concept_id in plan.deleted:
        (bundle.root / f"{concept_id}.md").unlink()
    return plan


__all__ = ["PruneResult", "plan_prune_entities", "prune_entities"]

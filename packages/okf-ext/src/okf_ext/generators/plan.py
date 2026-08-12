"""Plan a regeneration over named concepts in a bundle.

**Targets are named explicitly**, following `tables.plan_row`: "which concepts
should carry this" has no derivation, and inventing a predicate parameter for
a caller who already knows the answer is ceremony. That is also why this does
not sweep the bundle the way `plan_sections` does.

This module imports the shared layer, its own capability's model and pure
halves, and `okf_io`. It imports no sibling capability.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from okf_io import Bundle
from okf_io.bundle import INDEX_NAME

from okf_ext.generators.frontmatter import key_edits
from okf_ext.generators.model import Regeneration, RegenerationPlan, Render
from okf_ext.generators.regenerate import regenerate_body
from okf_ext.shape import SectionSet, TypeSections
from okf_ext.writing import Skipped, body_digest

#: The two ownership values that make a section writable by a generator.
#: `template` is granted even though the caller supplies nothing for it: the
#: declaration's placeholder wins, and refusing a redundant supply would make
#: the granted set differ from the declaration in a way callers must track.
_WRITABLE = ("generated", "template")


def _granted(declaration: TypeSections | None) -> tuple[frozenset[str], frozenset[str]]:
    """The frontmatter keys and section headings this declaration grants.

    A `None` declaration -- no `type`, or a type the set does not cover --
    grants nothing, which is what makes the missing-declaration case fall out
    of the same check rather than needing its own branch.
    """
    if declaration is None:
        return frozenset(), frozenset()
    keys = frozenset(declaration.frontmatter.owned) | frozenset(declaration.frontmatter.provenance)
    headings = frozenset(spec.heading for spec in declaration.sections if spec.ownership in _WRITABLE)
    return keys, headings


def _refuse(
    concept_id: str, type_name: str, declaration: TypeSections | None, keys: list[str], headings: list[str]
) -> str:
    """The `ValueError` message for a write the declaration does not grant.

    Names the concept, the offending key(s)/heading(s), and why -- enough for
    a caller to fix their declaration or their `Render` without reading this
    module's source.
    """
    if declaration is None:
        named = "no `type`" if not type_name else f"type {type_name!r}"
        return (
            f"{concept_id}: the declaration set has no declaration for {named}, so nothing may be written into it "
            f"(supplied frontmatter keys {keys}, sections {headings}). "
            f"Unlike `plan_sections`, this refuses rather than reporting: you named this concept and handed over "
            f"content for it, so silence would swallow the mistake."
        )
    parts = []
    if keys:
        parts.append(f"frontmatter keys {keys} are declared neither `owned` nor `provenance`")
    if headings:
        parts.append(f"sections {headings} are declared neither `generated` nor `template`")
    return (
        f"{concept_id}: the declaration for type {type_name!r} does not grant this write -- {'; and '.join(parts)}. "
        f"Everything undeclared is the human's. Grant it in the declaration, or stop computing it."
    )


def _index_member(directory: str) -> str:
    return f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME


def _granted_index(declaration: TypeSections | None) -> frozenset[str]:
    """The section headings an index declaration grants.

    There is no frontmatter half. §8 gives a bundle-root index exactly one
    legal key, `okf_version`, so an index declaration grants no keys at all
    and any frontmatter supplied for an index target hits the same
    granted-set refusal a concept's ungranted key does.
    """
    if declaration is None:
        return frozenset()
    return frozenset(spec.heading for spec in declaration.sections if spec.ownership in _WRITABLE)


def _refuse_index(directory: str, declaration: TypeSections | None, keys: list[str], headings: list[str]) -> str:
    member = _index_member(directory)
    named = "the bundle root index" if not directory else f"the `{directory}/` index"
    if declaration is None:
        return (
            f"{member}: the declaration set has no declaration for {named} (no `directories:` entry), so "
            f"nothing may be written into it (supplied frontmatter keys {keys}, sections {headings}). Declare "
            f"it in an underscore-prefixed file's `directories:` block, or stop computing it."
        )
    parts = []
    if keys:
        parts.append(
            f"frontmatter keys {keys} may never be written into an index -- OKF v0.2 §8 gives a bundle-root "
            f"index exactly one legal key, `okf_version`"
        )
    if headings:
        parts.append(f"sections {headings} are declared neither `generated` nor `template`")
    return (
        f"{member}: the declaration for {named} does not grant this write -- {'; and '.join(parts)}. "
        f"Everything undeclared is the human's."
    )


def plan_regenerate(
    bundle: Bundle,
    section_set: SectionSet,
    renders: Mapping[str, Render],
    index_renders: Mapping[str, Render] = MappingProxyType({}),
) -> RegenerationPlan:
    """Plan what *renders* would change about the named concepts in *bundle*.

    **The granted set is checked before any edit is computed, and it raises.**
    A frontmatter key that is neither `owned` nor `provenance`, or a section
    heading declared neither `generated` nor `template`, is a caller error --
    this is the invariant the whole capability exists to enforce, so it fails
    loudly at the boundary rather than quietly dropping the write.

    The skips come first, though: a target that is not a member has no type,
    no declaration and an empty granted set, so raising before skipping would
    turn "not in this bundle" into "you may write nothing here".

    **Idempotence surfaces as an empty plan.** A concept whose supplied values
    already match disk contributes no `Regeneration`, so `plan.is_empty` is the
    "nothing to do" signal and `plan.regenerations` names exactly what would
    change -- a preview a `dry_run` boolean could not express. That is the
    choice `tags`, `tables` and `sections` have all already made.

    Content problems never raise: an unreadable target becomes a `Skipped`
    with reason `unreadable`, an unparseable one `parse-error`, and a declared
    owned section that is not there `section-missing`. All three are existing
    members of `SkipReason`; this capability widens neither union.

    Raises `ValueError` for anything the declaration does not grant.

    *index_renders* is keyed by **directory id** (`""` for the bundle root)
    and targets `bundle.indexes` against `section_set.indexes`. A separate
    parameter rather than an overload of *renders*, because a directory id
    and a concept id genuinely collide: `packages` the lane and `packages.md`
    the concept share a key.

    An index `Regeneration` carries `concept_id=""` and its identity in
    `path` -- the precedent `okf_ext.bundle`'s `_skip` already sets, with the
    same reason: nothing here is a concept, so there is no id to carry.
    Index sections are planned with `regenerate_body(create_missing=True)`,
    so a granted section that is not yet in the document is created rather
    than skipped; see that function for why an index is the one case where
    that is right.
    """
    regenerations: list[Regeneration] = []
    skipped: list[Skipped] = []

    for concept_id in sorted(renders):
        render = renders[concept_id]
        member = f"{concept_id}.md"
        document = bundle.concepts.get(concept_id)

        if document is None:
            skipped.append(
                Skipped(
                    concept_id=concept_id,
                    path=member,
                    reason="unreadable",
                    detail=bundle.unreadable.get(member, "not a member of this bundle"),
                )
            )
            continue
        if document.parse_error is not None:
            skipped.append(
                Skipped(
                    concept_id=concept_id,
                    path=member,
                    reason="parse-error",
                    detail=f"{document.parse_error.kind}: {document.parse_error.message}",
                )
            )
            continue

        type_name = (document.fm.type or "").strip()
        declaration = section_set.types.get(type_name) if type_name else None
        granted_keys, granted_headings = _granted(declaration)
        ungranted_keys = sorted(set(render.frontmatter) - granted_keys)
        ungranted_headings = sorted(set(render.sections) - granted_headings)
        if ungranted_keys or ungranted_headings:
            raise ValueError(_refuse(concept_id, type_name, declaration, ungranted_keys, ungranted_headings))
        if declaration is None:
            # An empty `Render` against a concept with no declaration: the
            # caller asked for nothing and gets nothing. Every non-empty one
            # already raised above.
            continue

        edits = key_edits(document.fm_raw, declaration.frontmatter, render.frontmatter)
        after, section_edits, missing = regenerate_body(document.body, declaration, render.sections)
        skipped.extend(
            Skipped(concept_id=concept_id, path=member, reason="section-missing", detail=heading) for heading in missing
        )

        # A per-section splice can differ while the assembled body does not --
        # the trailing-newline fix in `regenerate_body` can undo the one line
        # a splice added. Checking the whole body here is what keeps property 1
        # ("regenerating with the values already on disk changes nothing")
        # true for a body with no trailing newline, not only for a canonical one.
        if after == document.body:
            section_edits = ()
        if not edits and not section_edits:
            continue

        regenerations.append(
            Regeneration(
                concept_id=concept_id,
                path=member,
                key_edits=edits,
                section_edits=section_edits,
                digest=body_digest(document.body),
                after=after,
            )
        )

    for directory in sorted(index_renders):
        render = index_renders[directory]
        member = _index_member(directory)
        document = bundle.indexes.get(directory)

        if document is None:
            skipped.append(
                Skipped(
                    concept_id="",
                    path=member,
                    reason="unreadable",
                    detail=bundle.unreadable.get(member, "not a member of this bundle"),
                )
            )
            continue
        if document.parse_error is not None:
            skipped.append(
                Skipped(
                    concept_id="",
                    path=member,
                    reason="parse-error",
                    detail=f"{document.parse_error.kind}: {document.parse_error.message}",
                )
            )
            continue

        declaration = section_set.indexes.get(directory)
        ungranted_keys = sorted(render.frontmatter)
        ungranted_headings = sorted(set(render.sections) - _granted_index(declaration))
        if ungranted_keys or ungranted_headings:
            raise ValueError(_refuse_index(directory, declaration, ungranted_keys, ungranted_headings))
        if declaration is None:
            continue

        # `create_missing=True`, so `missing` is always empty here -- an index
        # has no scaffolder to defer to.
        after, section_edits, _missing = regenerate_body(
            document.body, declaration, render.sections, create_missing=True
        )
        if after == document.body or not section_edits:
            continue

        regenerations.append(
            Regeneration(
                concept_id="",
                path=member,
                key_edits=(),
                section_edits=section_edits,
                digest=body_digest(document.body),
                after=after,
            )
        )

    skipped.sort(key=lambda item: (item.path, item.detail))
    return RegenerationPlan(root=bundle.root, regenerations=tuple(regenerations), skipped=tuple(skipped))


__all__ = ["plan_regenerate"]

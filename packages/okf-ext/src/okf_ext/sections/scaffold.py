"""Writing declared sections: the pure skeleton, and the bundle repair pair.

**Two writers off one declaration.** `render_skeleton` emits every declared
section, which is the "template for a new item" half; `plan_sections` /
`apply` insert only the *missing required* ones into documents that already
exist, which is the repair half. Both compose the same `_block`, so a document
created from a skeleton and a document repaired into one are byte-identical in
the lines this capability writes -- which is what keeps the rule's
placeholder-equality check meaningful across both paths. The one exception is
`_block`'s closing blank, which an append at the true end of a body with no
trailing newline drops rather than granting the body a terminator it never
had; see `_scaffold`.

**Nothing here creates a file.** `render_skeleton` returns a body string;
where that string goes is tier 3's decision. `okf_io.update_index()` defaults
`create_missing=False` for the same reason: creating a file is a bigger act
than editing one.

This module imports the shared layer (`okf_ext.body`, `okf_ext.splice`,
`okf_ext.writing`) and its own capability's model, and nothing else from its
own package.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath

from okf_io import Bundle, Document
from okf_io.bundle import INDEX_NAME, LOG_NAME
from okf_io.document import rendered_with_body
from ruamel.yaml.error import YAMLError

from okf_ext.body import Section, sections, split_lines
from okf_ext.sections.model import SectionInsert, SectionPlan, SectionSplice
from okf_ext.shape import SectionSet, SectionSpec, TypeSections
from okf_ext.splice import (
    assemble,
    bare_lines,
    dominant_newline,
    has_trailing_newline,
    insert,
    needs_gap,
)
from okf_ext.writing import ApplyResult, PendingWrite, Skipped, WriteFailure, body_digest, write_all


def _block(spec: SectionSpec) -> list[str]:
    """One section as bare lines: heading, blank, placeholder, blank.

    The trailing blank is what keeps the next block from reading as a lazy
    continuation of this one, and it is why a run of inserted sections lands
    contiguously without `needs_gap` stacking extra blanks between them.
    """
    lines = ["#" * spec.level + " " + spec.heading, ""]
    body = bare_lines(spec.placeholder)
    if body:
        lines.extend(body)
        lines.append("")
    return lines


def render_skeleton(type_sections: TypeSections, *, newline: str = "\n") -> str:
    """A body carrying **every** declared section, in declaration order.

    Required and optional alike: this is the skeleton a new item is created
    from, and an optional section is part of the shape even though it is never
    scaffolded into a document that already exists.

    *newline* is a parameter rather than a sniff because there is no body to
    sniff from -- no bundle, no filesystem, nothing on disk yet.

    Returns a body string and nothing else. It creates no file.
    """
    lines: list[str] = []
    for spec in type_sections.sections:
        lines.extend(_block(spec))
    return "".join(line + newline for line in lines)


def _present(body: str, spec: SectionSpec, levels: frozenset[int]) -> Section | None:
    """The first heading matching *spec*, considering declared levels only.

    Casefolded and stripped, reusing `okf_ext.body.find_section`'s matching
    rule -- but the level check is separate and explicit, because
    `find_section` is deliberately level-agnostic and the declaration is not.
    """
    wanted = spec.heading.casefold()
    for section in sections(body):
        if section.level in levels and section.heading.strip().casefold() == wanted:
            return section
    return None


def _declared_territory_end(body: str, after: Section, declaration: TypeSections, levels: frozenset[int]) -> int | None:
    """The `start` of the first **declared** heading that follows *after* in
    *body*, or `None` when no declared heading follows it.

    `Section.stop` is a generic markdown notion -- a section ends at the next
    heading of the same or shallower level, declared or not -- so it cannot
    answer "where does the last *declared* section's territory end", only
    "where does the next heading of any kind begin". This walks the document
    forward from *after* instead, skipping every heading that is not itself
    declared, and stops at the first one that is. Undeclared headings
    interleaved in between are exactly what this is built to see past.
    """
    wanted = {spec.heading.casefold() for spec in declaration.sections}
    for section in sections(body):
        if section.start <= after.start:
            continue
        if section.level in levels and section.heading.strip().casefold() in wanted:
            return section.start
    return None


def _position(body: str, declaration: TypeSections, index: int, levels: frozenset[int], total: int) -> int:
    """Where the section at *index* belongs, 1-based and body-relative.

    Declaration order earns its keep here, and only here (§5.4: reordering
    sections is house style, not a defect, so there is no `out-of-order`
    code). Three fallbacks, in order:

    1. after the last **earlier** declared section present -- specifically,
       at the end of *its* declared territory (the first declared heading
       that follows it, or the end of the body if none does), not merely at
       its own `stop`. See `_declared_territory_end` for why that
       distinction matters: an earlier section's raw `stop` is bounded by
       *any* following heading, declared or not, so anchoring there would
       plant the insert flush against an intervening undeclared one instead
       of skipping past it to where declared structure resumes.
    2. failing that (no earlier declared section present at all), before the
       `start` of the first **later** one present,
    3. failing both, at the end of the body.

    Only declared sections are ever consulted for a position -- undeclared
    headings interleaved in the document do not perturb it.
    """
    for earlier in reversed(declaration.sections[:index]):
        found = _present(body, earlier, levels)
        if found is not None:
            end = _declared_territory_end(body, found, declaration, levels)
            return end if end is not None else total + 1
    for later in declaration.sections[index + 1 :]:
        found = _present(body, later, levels)
        if found is not None:
            return found.start
    return total + 1


def _scaffold(body: str, declaration: TypeSections) -> tuple[str, tuple[SectionInsert, ...]]:
    """*body* with every missing **required** section inserted.

    Each insert is placed against the document **as amended by the previous
    one**, which is what makes a run of missing sections land in declaration
    order and contiguously rather than each one racing for the same anchor.
    """
    newline = dominant_newline(body)
    lines: list[str] = list(split_lines(body))
    trailing = has_trailing_newline(lines)
    levels = frozenset(spec.level for spec in declaration.sections)
    inserts: list[SectionInsert] = []

    for index, spec in enumerate(declaration.sections):
        if not spec.required:
            continue
        current = assemble(lines, newline, trailing)
        if _present(current, spec, levels) is not None:
            continue
        at = _position(current, declaration, index, levels, len(lines))
        gap = [""] if needs_gap(lines, at) else []
        lines = insert(lines, at, [*gap, *_block(spec)], newline)
        # `line` is `at` -- the separating blank when one was needed,
        # otherwise the heading itself -- never `at + len(gap)`. The gap is
        # still new content this wrote, so a caller diffing from `line` must
        # see a contiguous inserted span with no gap-shaped hole above it.
        inserts.append(SectionInsert(heading=spec.heading, line=at))

    # `_block` always closes with a blank sentinel line, terminated like every
    # other inserted line. When the last insert lands at the true end of a
    # body with no trailing newline, that sentinel stacks a second terminator
    # onto the one `insert` already forces onto the line before it -- and
    # `assemble` only ever undoes one. Dropping the sentinel here (it carries
    # no text) leaves the newly-last line as the one `assemble` knows how to
    # de-terminate, so a body with no trailing newline still has none.
    if not trailing and lines and lines[-1] == newline:
        lines = lines[:-1]

    return assemble(lines, newline, trailing), tuple(inserts)


def _unreadable_skips(bundle: Bundle) -> list[Skipped]:
    """Members `load_bundle` could not decode, as `Skipped`s.

    They never became concepts, so iterating `bundle.concepts` cannot see
    them -- `bundle.unreadable` is the only place they surface. `index.md` and
    `log.md` are excluded: they are not concepts, so an unreadable one is not
    this capability's business.
    """
    found: list[Skipped] = []
    for member, reason in sorted(bundle.unreadable.items()):
        if not member.endswith(".md") or PurePosixPath(member).name in {INDEX_NAME, LOG_NAME}:
            continue
        found.append(Skipped(concept_id=member[:-3], path=member, reason="unreadable", detail=reason))
    return found


def plan_sections(bundle: Bundle, section_set: SectionSet) -> SectionPlan:
    """Plan every missing **required** section into every concept in *bundle*.

    **Only `required` sections are scaffolded.** Optional sections appear in
    `render_skeleton`'s output and nowhere else -- scaffolding them would
    create sections nobody needs, which is what the three-class model exists
    to avoid.

    **Idempotence surfaces as an empty plan.** A document already carrying
    every required section contributes no splice at all, so `plan.is_empty` is
    the "nothing to do" signal and `plan.splices` names exactly what would
    change -- a preview a boolean return could not express, which is why there
    is no `dry_run` flag. That is the choice `tables.plan_row` already made.

    **A document with no `type`, or a type with no declaration, is not a skip
    and is not reported at all.** It was read perfectly well; it simply has no
    declaration to be measured against, and
    `sections.no-declaration-for-type` is where that gap surfaces. The writer
    does not duplicate the rule.

    Content problems never raise: an unparseable concept becomes a `Skipped`
    with reason `parse-error`, and a member `load_bundle` could not decode
    becomes one with reason `unreadable`. Those are the only two reasons this
    capability emits, and `SkipReason` needs no widening for them.
    """
    splices: list[SectionSplice] = []
    skipped: list[Skipped] = _unreadable_skips(bundle)

    for concept_id in sorted(bundle.concepts):
        document = bundle.concepts[concept_id]
        member = f"{concept_id}.md"
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
        if declaration is None:
            continue
        after, inserts = _scaffold(document.body, declaration)
        if not inserts:
            continue
        splices.append(
            SectionSplice(
                concept_id=concept_id,
                path=member,
                inserts=inserts,
                digest=body_digest(document.body),
                after=after,
            )
        )

    skipped.sort(key=lambda item: item.path)
    return SectionPlan(root=bundle.root, splices=tuple(splices), skipped=tuple(skipped))


def _body_committer(document: Document, body: str) -> Callable[[], None]:
    """What `write_all` runs once this document's bytes have landed.

    `set_body` writes through to the `Split` as well as the `body` field, so
    the in-memory bundle agrees with disk immediately, with no reload, for
    exactly the documents written and no others.
    """

    def commit() -> None:
        document.set_body(body)

    return commit


def apply(bundle: Bundle, plan: SectionPlan) -> ApplyResult:
    """Write *plan* against *bundle*.

    **Content failures are refused per document; siblings still write.** The
    two I/O regimes -- probe/staging all-or-nothing, and per-document commit
    -- belong to `okf_ext.writing.write_all`, which is where they are
    documented and which this inherits unchanged.

    **Staleness is a body digest**, over the whole body: the scaffold is a
    deterministic function of all of it, and a change anywhere can move the
    line an insert lands on. `kind="stale"` is the one failure worth
    re-planning over.

    A plan naming one concept twice is refused with `duplicate-edit`. No plan
    `plan_sections` builds can carry one (it iterates the concept mapping
    once), but a hand-built plan could, and both splices were computed against
    the same body -- applying them in sequence would silently discard the
    first.

    Raises `ValueError` for a plan built against a different bundle: the
    digests and line numbers in a plan mean nothing anywhere else.
    """
    if Path(plan.root).resolve() != Path(bundle.root).resolve():
        raise ValueError(
            f"Plan was built against a different bundle ({plan.root}), not {bundle.root}. "
            f"A plan's splices mean nothing outside the bundle it was planned against."
        )

    grouped: dict[str, list[SectionSplice]] = {}
    for splice in plan.splices:
        grouped.setdefault(splice.concept_id, []).append(splice)

    failed: list[WriteFailure] = []
    pending: list[PendingWrite] = []

    for concept_id, splices in sorted(grouped.items()):
        member = f"{concept_id}.md"
        document = bundle.concepts.get(concept_id)
        if document is None or document.path is None:
            failed.append(
                WriteFailure(path=member, kind="not-a-member", error="concept is not a member of this bundle")
            )
            continue
        if document.parse_error is not None:
            failed.append(
                WriteFailure(
                    path=member,
                    kind="parse-error",
                    error=(
                        f"cannot mutate a document that failed to parse "
                        f"({document.parse_error.kind}): {document.parse_error.message}"
                    ),
                )
            )
            continue
        if len(splices) > 1:
            failed.append(
                WriteFailure(
                    path=member,
                    kind="duplicate-edit",
                    error=(
                        "plan carries more than one splice for this concept; "
                        "refusing rather than silently applying only the last"
                    ),
                )
            )
            continue

        splice = splices[0]
        if body_digest(document.body) != splice.digest:
            failed.append(
                WriteFailure(
                    path=member,
                    kind="stale",
                    error="stale plan: the body changed since it was planned; re-plan against the current bundle",
                )
            )
            continue

        try:
            rendered = rendered_with_body(document, splice.after)
        except (YAMLError, ValueError, RecursionError) as exc:
            failed.append(WriteFailure(path=member, kind="serialize-error", error=str(exc)))
            continue

        pending.append(
            PendingWrite(
                member=member,
                path=document.path,
                rendered=rendered,
                on_written=_body_committer(document, splice.after),
            )
        )

    return write_all(pending, failed=failed, skipped=plan.skipped)


__all__ = ["apply", "plan_sections", "render_skeleton"]

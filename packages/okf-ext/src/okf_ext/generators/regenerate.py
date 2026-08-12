"""The two-class section model: rewrite what the declaration grants, carry
everything else through byte for byte.

Pure. No bundle, no document, no filesystem -- a body string in, a body
string plus the edits out. That is what lets the acceptance properties run
line-ending and trailing-newline variants over the corpus without touching
disk.

**Nothing here creates a section.** A declared owned section that is absent
is reported in `missing` and skipped; `sections.plan_sections` is what creates
required sections, and the composition is scaffold-then-regenerate.

**`create_missing` is the one exception, and it is for index documents.**
A concept that is missing a declared section has a scaffolder --
`sections.plan_sections`, and the composition is scaffold-then-regenerate.
An index document has none: `plan_sections` walks `bundle.concepts`, and
the bundle scaffold writes `index.md` as frontmatter plus an H1. So for an
index a granted-but-absent section is not a division of labour, it is a
dead end -- the grant could never be exercised. With *create_missing* the
section is appended at the end of the body (heading, blank, content),
separated by a blank line when the line above is not already one, and
reported as a `SectionEdit` rather than in `missing`. It is appended
rather than positioned in declaration order because an index's
declaration is merged from several files, and no package may claim a
position among another package's sections.

This module imports the shared layer and its own capability's model, and
nothing else from its own package.
"""

from __future__ import annotations

from collections.abc import Mapping

from okf_ext.body import Section, sections, split_lines
from okf_ext.generators.model import SectionEdit
from okf_ext.shape import SectionSpec, TypeSections
from okf_ext.splice import (
    assemble,
    bare_lines,
    dominant_newline,
    has_trailing_newline,
    insert,
    needs_gap,
    replace,
)


def _locate(body: str, spec: SectionSpec, levels: frozenset[int]) -> Section | None:
    """The first heading matching *spec*, considering declared levels only.

    Casefolded and stripped, matching `sections/scaffold.py`'s `_present` --
    restated rather than imported, because that one is private to a sibling
    capability and the independence contract forbids reaching for it. Six
    lines is the honest price of the boundary; hoisting it would widen
    `okf_ext.shape` to hold behaviour rather than declarations.

    The one textual difference from `_present` is that this also strips
    *spec*'s own heading. That cannot change the outcome for a declaration
    `load_sections` produced -- the loader stores `heading` already stripped,
    so the two agree on every real `SectionSet` -- and it keeps the function
    total over a hand-built `SectionSpec`, which the tests construct directly.

    **Precondition, not guarded:** *supplied* content is assumed not to
    contain a line that itself parses as a declared heading at a declared
    level. `regenerate_body` re-runs this scan against the amended body
    between edits, so an already-spliced line that reads as, say, `##
    Sources` is indistinguishable from the real thing on the next iteration
    -- the scan is first-match, document-order, with no notion of "the
    heading `_locate` itself just wrote in". A later declared section whose
    heading collides this way is found early, at the injected line, and
    written there; the real heading further down is left with its stale
    content. Not checked here, deliberately -- whether this becomes a guard
    is a call above this module's pay grade.
    """
    wanted = spec.heading.strip().casefold()
    for section in sections(body):
        if section.level in levels and section.heading.strip().casefold() == wanted:
            return section
    return None


def _needs_trailing_sentinel(lines: list[str], found: Section) -> bool:
    """Whether the replacement should close with a blank sentinel line.

    The sentinel is `_block`'s closing blank, restated: it is what separates
    this section from whatever comes next. Whether "whatever comes next"
    exists is a question about *lines*, not about the declaration, so this
    looks at the document, not the spec.

    `found`'s own span already tells us the answer. When the span's tail --
    `max(found.stop, found.body_start - 1)`, the same index `splice.replace`
    slices to -- lands short of the document's end, there is more content
    below (another heading, or a run of lines the parser folded into this
    span) and it always had a separating blank, because `Section.stop` is
    computed as "one line short of the next heading" -- so the sentinel is
    always written back.

    When the span's tail **is** the document's end, there is nothing below to
    separate from. Manufacturing a blank there is exactly the corpus's
    `full.md` regression: its `About this page` section runs to end of body
    with no trailing blank, and appending one anyway is a byte nobody chose,
    on every single regeneration. So the sentinel is dropped there --
    *unless* the span's own last existing line was already blank, in which
    case that blank is this section's own trailing blank (the "stop is not
    trimmed past trailing blank lines" case) and the sentinel is what
    restores it rather than inventing it.
    """
    tail = max(found.stop, found.body_start - 1)
    if tail < len(lines):
        return True
    return found.stop >= found.body_start and lines[found.stop - 1].strip() == ""


def _content(spec: SectionSpec, supplied: Mapping[str, str]) -> str | None:
    """What this run writes into *spec*, or `None` to leave it alone.

    A `template` section's content is its declared placeholder, always -- the
    generator supplies nothing for it, and the declaration is where the fact
    belongs rather than re-derived as `spec.placeholder` at every call site.
    """
    if spec.ownership == "template":
        return spec.placeholder
    if spec.ownership == "generated" and spec.heading in supplied:
        return supplied[spec.heading]
    return None


def regenerate_body(
    body: str,
    declaration: TypeSections,
    supplied: Mapping[str, str],
    *,
    create_missing: bool = False,
) -> tuple[str, tuple[SectionEdit, ...], tuple[str, ...]]:
    """*body* with every owned section rewritten.

    Returns the new body, the edits in document order, and the headings that
    were owned-and-due-a-write but not found -- the caller turns those into
    `Skipped(reason="section-missing")`.

    Each edit is planned against the document **as amended by the previous
    one**, following `_scaffold`, so line numbers stay meaningful as the body
    shifts under them.

    The replacement is a blank line, `bare_lines(text)`, and -- when
    `_needs_trailing_sentinel` says the document has more below this section
    -- a closing blank sentinel, spliced over `body_start … stop`. That is
    `sections/scaffold.py`'s `_block` **minus the heading**, and it is that
    shape for a reason: **a document scaffolded and then regenerated must be
    byte-identical to one regenerated directly.** Otherwise the composition
    this capability recommends produces a document that differs from a
    regenerate-only run by whitespace nobody chose, and that difference shows
    up as a spurious edit on the next run.

    The leading blank is unconditional: `body_start` is defined as the first
    line *after* the heading, so a heading is always followed by more
    document (its own former body, or the next heading), and the blank
    belongs there regardless of what follows. The trailing blank is not --
    see `_needs_trailing_sentinel` for why a section that runs to the true
    end of the body does not automatically get one back.

    `Section.stop` is inclusive and is not trimmed back past trailing blank
    lines, so a section that is *not* last owns the blank that separates it
    from the next heading, and the splice always replaces it.

    See `_locate`'s docstring for the heading-collision precondition this
    relies on: *supplied* content that itself contains a line parsing as a
    later declared heading will be matched by that later iteration's scan
    instead of the real heading, silently misplacing the write.
    """
    newline = dominant_newline(body)
    lines: list[str] = list(split_lines(body))
    trailing = has_trailing_newline(lines)
    levels = frozenset(spec.level for spec in declaration.sections)
    edits: list[SectionEdit] = []
    missing: list[str] = []

    for spec in declaration.sections:
        text = _content(spec, supplied)
        if text is None:
            continue
        found = _locate(assemble(lines, newline, trailing), spec, levels)
        if found is None:
            if not create_missing:
                missing.append(spec.heading)
                continue
            at = len(lines) + 1
            gap = [""] if needs_gap(lines, at) else []
            heading = "#" * spec.level + " " + spec.heading
            lines = insert(lines, at, [*gap, heading, "", *bare_lines(text)], newline)
            edits.append(SectionEdit(heading=spec.heading, line=at))
            continue
        sentinel = [""] if _needs_trailing_sentinel(lines, found) else []
        candidate = replace(lines, found.body_start, found.stop, ["", *bare_lines(text), *sentinel], newline)
        if candidate == lines:
            continue
        lines = candidate
        edits.append(SectionEdit(heading=spec.heading, line=found.body_start))

    # A defensive backstop, not the primary fix -- `_needs_trailing_sentinel`
    # already declines to manufacture a sentinel at the true end of a body
    # with nothing existing to restore, which is what closes the §5.4
    # hazard's usual case. This guards the one combination that check cannot
    # see from the inside: a body with no trailing newline whose absolute
    # last line is nonetheless blank (pure whitespace, unterminated) --
    # `_needs_trailing_sentinel` reads that as "restore the existing blank"
    # and emits a bare sentinel that becomes this edit's last line. `replace`
    # terminates every line it writes, so that bare sentinel becomes a
    # standalone terminator stacked onto the one `assemble` already undoes
    # for a body with no trailing newline. Dropping it here (it carries no
    # text) leaves the newly-last line as the one `assemble` knows how to
    # de-terminate. Inherited from `sections/scaffold.py:201`; the interaction
    # is invisible in either module read alone.
    if not trailing and lines and lines[-1] == newline:
        lines = lines[:-1]

    return assemble(lines, newline, trailing), tuple(edits), tuple(missing)


__all__ = ["regenerate_body"]

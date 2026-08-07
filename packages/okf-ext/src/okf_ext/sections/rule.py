"""The rule that turns a loaded `SectionSet` into `Finding`s over a bundle.

    validate(bundle, today=..., extra_rules=[section_rule(section_set)])

The only `Finding` source in this capability, and the only thing here that
touches a bundle. Nothing raises for content: a missing section is a
`Finding`, never an exception.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from okf_io import Finding, Rule, RuleContext, Severity

from okf_ext.body import Section, sections
from okf_ext.sections.model import SectionSet, TypeSections

#: The topic prefix this rule set claims. `validate()` raises the moment an
#: external rule emits a built-in prefix, and `sections` collides with none of
#: the twelve in use (okf-io's eight, plus `tags`, `schemas`, `render`,
#: `health`). The module name is the prefix, as everywhere else in this
#: workspace.
TOPIC = "sections"

CODES = (
    "sections.missing",  # a required section has no matching heading
    "sections.unfilled",  # the section exists but is empty, or equals its placeholder
    "sections.unexpected",  # an undeclared heading, only under `additional_sections: false`
    "sections.no-declaration-for-type",  # the set has no declaration for this concept's type
)

#: Unpacked from `CODES` rather than re-typed, so a code-string edit to one
#: cannot silently drift from the other -- the habit `tags/vocabulary.py` set.
_CODE_MISSING, _CODE_UNFILLED, _CODE_UNEXPECTED, _CODE_NO_DECLARATION = CODES


def _normalized(text: str) -> str:
    """*text*'s comparable form for the unfilled check.

    Line endings normalised, each line stripped, leading and trailing blank
    lines dropped. Deliberately **not** a marker scheme: nothing leaks
    into the rendered document, and nothing collides with
    `render.angle-bracket`, which already flags bare `<placeholder>` text as
    HTML that Obsidian eats.
    """
    flat = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in flat.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _considered(body: str, declaration: TypeSections) -> tuple[Section, ...]:
    """Every heading whose **level** the declaration names.

    A `### Sub-heading` beneath `## Plan` is therefore never `unexpected`.
    Without this, `additional_sections: false` makes every nested heading in a
    corpus a finding, and the setting is unusable on real prose. Headings
    inside blockquotes are already excluded by `okf_ext.body.sections()`.
    """
    levels = {spec.level for spec in declaration.sections}
    return tuple(section for section in sections(body) if section.level in levels)


def _findings(
    body: str,
    declaration: TypeSections,
    *,
    source: str,
    path: str,
    offset: int,
    severity: Severity,
) -> Iterator[Finding]:
    considered = _considered(body, declaration)
    declared = {spec.heading.casefold() for spec in declaration.sections}

    for spec in declaration.sections:
        wanted = spec.heading.casefold()
        found = next((s for s in considered if s.heading.strip().casefold() == wanted), None)
        if found is None:
            if spec.required:
                yield Finding(
                    code=_CODE_MISSING,
                    severity=severity,
                    message=f"Required section `{spec.heading}` is missing.",
                    spec=source,
                    path=path,
                    # The fault is the document's, not any line's, so naming
                    # one would be a guess -- and it keeps this module free of
                    # the insertion algorithm in `scaffold.py`.
                    line=None,
                )
            continue
        # Optional sections are declared so a legitimate heading is not
        # "extra"; they are never scaffolded and never warned about (§3.3).
        # `seeded_is_complete` says the placeholder is a valid end state.
        if not spec.required or spec.seeded_is_complete:
            continue
        content = _normalized(found.slice(body))
        if content and content != _normalized(spec.placeholder):
            continue
        detail = "is empty" if not content else "still carries its placeholder"
        yield Finding(
            code=_CODE_UNFILLED,
            severity=severity,
            message=f"Section `{found.heading}` {detail}.",
            spec=source,
            path=path,
            line=found.start + offset,
        )

    if declaration.additional_sections:
        return
    for section in considered:
        if section.heading.strip().casefold() not in declared:
            yield Finding(
                code=_CODE_UNEXPECTED,
                severity=severity,
                message=f"Section `{section.heading}` is not declared by `{source}`.",
                spec=source,
                path=path,
                line=section.start + offset,
            )


def section_rule(section_set: SectionSet, *, severity: Severity = "warn") -> Rule:
    """Build an `okf_io.Rule` that checks body shape against *section_set*.

    **`severity` defaults to `warn`**, for the reason `schema_rule` gives
    verbatim: `Report.ok` is a claim about OKF v0.2 conformance, and a house
    rule has no business making a conformant bundle look otherwise. The knob
    exists because okf-io's `strict=True` does not serve this case -- it
    promotes *every* warning, including the deliberately-`warn`
    `links.broken`.

    `sections.no-declaration-for-type` is **always `warn`** regardless,
    mirroring `schemas.no-schema-for-type`: it reports a coverage gap in the
    declaration set, not a violation by the document.

    Line numbers are file lines, not body lines: `okf_ext.body` spans are
    body-relative and `document.body_line_offset` is what turns one into the
    other, exactly as `render/rule.py` does it.

    Documents okf-io could not parse are skipped rather than re-reported, and
    a document with no `type`, or an empty one, is skipped because there is
    nothing to dispatch on. Both match `schema_rule` exactly.
    """
    set_name = section_set.root.name or str(section_set.root)

    def rule(context: RuleContext) -> Iterable[Finding]:
        for concept_id in sorted(context.bundle.concepts):
            document = context.bundle.concepts[concept_id]
            if document.parse_error is not None:
                continue
            type_name = (document.fm.type or "").strip()
            if not type_name:
                continue
            path = f"{concept_id}.md"
            declaration = section_set.types.get(type_name)
            if declaration is None:
                yield Finding(
                    code=_CODE_NO_DECLARATION,
                    severity="warn",
                    message=f"No section declaration for type `{type_name}` in `{set_name}`.",
                    spec=set_name,
                    path=path,
                    line=document.frontmatter_line("type"),
                )
                continue
            yield from _findings(
                document.body,
                declaration,
                source=section_set.sources[type_name],
                path=path,
                offset=document.body_line_offset,
                severity=severity,
            )

    return rule

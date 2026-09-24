"""The rule that turns bundle-wide coherence facts into `Finding`s.

    validate(bundle, today=..., extra_rules=[health_rule()])

The only `Finding` source in this capability, and the only thing here that
touches a bundle. Nothing raises for content.

**No clock, anywhere.** `health.log-gap` reads `context.today`, which
`validate()` requires from its own caller. This module never calls
`date.today()`, and `log_gap_days` is a factory argument rather than an
`ExtContext` field: `ExtContext` is the shared cross-cutting layer, and a
threshold used by exactly one code of one capability is not cross-cutting.

**Documents okf-io could not parse are skipped by every code.** One habit per
module: okf-io already emitted `frontmatter.unparseable`, and a bundle carrying
an unparseable log has a bigger problem than a stale one.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from okf_io import Finding, Rule, RuleContext, Severity, parse_log

#: The topic prefix this rule set claims. `validate()` raises the moment an
#: external rule emits a built-in prefix, and `health` collides with none of the
#: eight (computation, frontmatter, legacy, lifecycle, links, provenance,
#: reserved, trust). The module name is the prefix, as in okf-io.
TOPIC = "health"

CODES = (
    "health.uncited",  # no other concept's prose links to this one
    "health.log-gap",  # the newest dated log section is old, or there is none
)

#: Unpacked from `CODES` rather than re-typed, so a code-string edit to one
#: cannot silently drift from the other -- the habit `tags/vocabulary.py` set.
_CODE_UNCITED, _CODE_LOG_GAP = CODES

#: `Finding.spec` is "the thing that says so". These codes cite no OKF section
#: -- an uncited concept is a conformant concept -- so they cite the module that
#: defines them.
_SPEC = "okf_ext.health"

#: Restated rather than imported from `okf_io.bundle`: that constant is
#: public-but-unexported, and one string literal is cheaper than a dependency on
#: a name the core never promised.
_LOG_NAME = "log.md"


def _uncited(context: RuleContext, severity: Severity) -> Iterator[Finding]:
    """Concepts no other concept's **prose** cites.

    `LinkGraph.backlinks` excludes indexes as link sources, on purpose: an OKF
    §8 index enumerating its own directory is a table of contents, not a
    citation. That is narrower than wiki-io's `orphans`, which counted index
    inbound links -- hence the different name. Counting index links would
    rescue nearly every document and leave the code reporting almost nothing.
    """
    for concept_id in sorted(context.bundle.concepts):
        document = context.bundle.concepts[concept_id]
        if document.parse_error is not None:
            continue
        if context.links.backlinks.get(concept_id):
            continue
        yield Finding(
            code=_CODE_UNCITED,
            severity=severity,
            message="No other concept's prose cites this one.",
            spec=_SPEC,
            path=f"{concept_id}.md",
            line=None,
        )


def _log_gaps(context: RuleContext, severity: Severity, log_gap_days: int) -> Iterator[Finding]:
    """Logs whose newest dated section is old, and logs that record nothing.

    Undated sections are ignored rather than reported: okf-io's
    `reserved.log-heading-not-date` already reports a log heading that is not a
    date. A bundle with no logs at all yields nothing -- not an error, not a
    finding.
    """
    for directory in sorted(context.bundle.logs):
        document = context.bundle.logs[directory]
        if document.parse_error is not None:
            continue
        path = f"{directory}/{_LOG_NAME}" if directory else _LOG_NAME
        # `(date, section)` pairs rather than sections: the date is what `max`
        # keys on, and pairing narrows it out of `date | None` for the type
        # checker at the same time.
        dated = [(section.date, section) for section in parse_log(document).sections if section.date is not None]
        if not dated:
            yield Finding(
                code=_CODE_LOG_GAP,
                severity=severity,
                message="Log carries no dated section.",
                spec=_SPEC,
                path=path,
                line=None,
            )
            continue
        newest_date, newest = max(dated, key=lambda pair: pair[0])
        gap = (context.today - newest_date).days
        if gap > log_gap_days:
            yield Finding(
                code=_CODE_LOG_GAP,
                severity=severity,
                message=(
                    f"Newest dated section is `{newest_date.isoformat()}`, "
                    f"{gap} days before `{context.today.isoformat()}`."
                ),
                spec=_SPEC,
                path=path,
                line=newest.line + document.body_line_offset,
            )


def health_rule(*, severity: Severity = "warn", log_gap_days: int = 14) -> Rule:
    """Build an `okf_io.Rule` that checks a bundle's internal coherence.

    **Every code is `warn` by default.** `Report.ok` is a claim about OKF v0.2
    conformance, and neither of these two describes a conformance failure -- an
    uncited concept is a conformant concept. The knob exists because okf-io's
    `strict=True` promotes *every* warning, including the deliberately `warn`
    `links.broken`, so a team wanting CI red on an uncited concept should not
    also get CI red on a dead link.

    *log_gap_days* defaults to 14, the threshold the wiki-io linter used.
    """

    def rule(context: RuleContext) -> Iterable[Finding]:
        yield from _uncited(context, severity)
        yield from _log_gaps(context, severity, log_gap_days)

    return rule

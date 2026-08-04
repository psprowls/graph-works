"""Rules for `sources` and the footnote join (OKF v0.2 §5.1)."""

from __future__ import annotations

from collections.abc import Iterable

from okf_io._rules._common import concepts, member_path
from okf_io.validate import Finding, Rule, RuleContext

CODES: tuple[str, ...] = (
    "provenance.source-resource-missing",
    "provenance.usage-count-unframed",
    "provenance.footnote-unjoined",
    "provenance.source-uncited",
)


def source_entries(ctx: RuleContext) -> Iterable[Finding]:
    """§5.1: `resource` is REQUIRED within an entry, and `usage_count` needs a window."""
    for concept_id, document in concepts(ctx):
        path = member_path(concept_id)
        frontmatter = document.fm
        for position, source in enumerate(frontmatter.sources):
            if not (source.resource or "").strip():
                yield Finding(
                    "provenance.source-resource-missing",
                    "error",
                    f"`sources[{position}]` has no `resource`",
                    "§5.1",
                    path,
                )
            unframed = (
                source.usage_count is not None
                and source.usage_window is None
                and frontmatter.usage_window is None
            )
            if unframed:
                yield Finding(
                    "provenance.usage-count-unframed",
                    "warn",
                    f"`sources[{position}].usage_count` has no `usage_window` to frame it",
                    "§5.1",
                    path,
                )


def footnote_join(ctx: RuleContext) -> Iterable[Finding]:
    """§5.1's per-claim attribution, checked in both directions.

    The body-side key set is the union of footnote references *and*
    definitions. A definition carrying no reference still declares the source;
    counting only references would fire ``source-uncited`` on
    ``acme_retail/metrics/gross-margin.md``, which does exactly that.
    """
    for concept_id, document in concepts(ctx):
        path = member_path(concept_id)
        labels = ctx.links.bodies[concept_id].footnote_labels
        identifiers = {
            source.id.strip() for source in document.fm.sources if source.id and source.id.strip()
        }
        for label in sorted(labels - identifiers):
            yield Finding(
                "provenance.footnote-unjoined",
                "warn",
                f"Footnote `[^{label}]` matches no `sources[].id`",
                "§5.1",
                path,
            )
        for identifier in sorted(identifiers - labels):
            yield Finding(
                "provenance.source-uncited",
                "warn",
                f"`sources[].id` `{identifier}` is cited by no footnote",
                "§5.1",
                path,
            )


RULES: tuple[Rule, ...] = (source_entries, footnote_join)

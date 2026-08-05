"""Rules for `generated`, `verified`, and the actor convention (OKF v0.2 §5.2, §7)."""

from __future__ import annotations

from collections.abc import Iterable

from okf_io._rules._common import concepts, member_path
from okf_io.models import Actor
from okf_io.validate import Finding, Rule, RuleContext

CODES: tuple[str, ...] = (
    "trust.generated-by-missing",
    "trust.timestamp-not-iso",
    "trust.verified-entry-incomplete",
    "trust.actor-convention",
)


def generated_block(ctx: RuleContext) -> Iterable[Finding]:
    """§5.2: a `generated` block records both `by` and `at`.

    Keyed off ``fm_raw``, not the view. The v0.1 read fallback synthesises
    a ``generated`` with no ``by`` from a v0.1 top-level ``timestamp``, and an
    unmigrated v0.1 concept is a ``legacy.timestamp`` hint -- not a conformance
    error. §11 forbids rejecting a concept for that.
    """
    for concept_id, document in concepts(ctx):
        if document.fm_raw.get("generated") is None:
            continue
        generated = document.fm.generated
        if generated is not None and generated.by is None:
            yield Finding(
                "trust.generated-by-missing",
                "error",
                "`generated` is present without `by`",
                "§5.2",
                member_path(concept_id),
            )


def timestamps(ctx: RuleContext) -> Iterable[Finding]:
    """§5.2: a trust timestamp that will not parse as ISO 8601.

    The view keeps the raw string in ``at`` and leaves ``at_dt`` None when it
    could not be parsed, so this reads the pair rather than re-parsing.

    Keyed off ``fm_raw`` for the generated block, just like ``generated_block``:
    if ``generated`` is missing from ``fm_raw``, the value reached the view
    through the read fallback from a v0.1 top-level ``timestamp``, and the
    message should name that original field. If ``generated`` IS present, keep
    naming ``generated.at``. An unparseable timestamp is worth reporting either
    way; only the field name reflects what the author wrote.
    """
    for concept_id, document in concepts(ctx):
        path = member_path(concept_id)
        frontmatter = document.fm
        generated = frontmatter.generated
        has_explicit_generated = document.fm_raw.get("generated") is not None

        if generated is not None and generated.at is not None and generated.at_dt is None:
            field_name = "generated.at" if has_explicit_generated else "timestamp"
            yield Finding(
                "trust.timestamp-not-iso",
                "warn",
                f"`{field_name}` is not ISO 8601: {generated.at!r}",
                "§5.2",
                path,
            )
        for position, event in enumerate(frontmatter.verified):
            if event.at is not None and event.at_dt is None:
                yield Finding(
                    "trust.timestamp-not-iso",
                    "warn",
                    f"`verified[{position}].at` is not ISO 8601: {event.at!r}",
                    "§5.2",
                    path,
                )


def verified_entries(ctx: RuleContext) -> Iterable[Finding]:
    """§5.2: a `verified[]` entry records who confirmed it and when."""
    for concept_id, document in concepts(ctx):
        path = member_path(concept_id)
        for position, event in enumerate(document.fm.verified):
            missing = [
                name
                for name, present in (("by", event.by is not None), ("at", event.at is not None))
                if not present
            ]
            if missing:
                names = " and ".join(f"`{name}`" for name in missing)
                yield Finding(
                    "trust.verified-entry-incomplete",
                    "warn",
                    f"`verified[{position}]` is missing {names}",
                    "§5.2",
                    path,
                )


def actor_convention(ctx: RuleContext) -> Iterable[Finding]:
    """§7: every recorded identity uses one of the three forms.

    Covers `sources[].author` as well as the two trust fields -- §5.1 says an
    author is "in the actor convention (§7)", so holding it to a different
    standard would be the checker disagreeing with the spec.
    """
    for concept_id, document in concepts(ctx):
        path = member_path(concept_id)
        frontmatter = document.fm
        actors: list[tuple[str, Actor]] = []
        if frontmatter.generated is not None and frontmatter.generated.by is not None:
            actors.append(("generated.by", frontmatter.generated.by))
        for position, event in enumerate(frontmatter.verified):
            if event.by is not None:
                actors.append((f"verified[{position}].by", event.by))
        for position, source in enumerate(frontmatter.sources):
            if source.author is not None:
                actors.append((f"sources[{position}].author", source.author))
        for field, actor in actors:
            if actor.kind == "unknown":
                yield Finding(
                    "trust.actor-convention",
                    "warn",
                    f"`{field}` `{actor.raw}` matches none of `human:`, `process:`, "
                    f"or `<producer>/<version>`",
                    "§7",
                    path,
                )


RULES: tuple[Rule, ...] = (generated_block, timestamps, verified_entries, actor_convention)

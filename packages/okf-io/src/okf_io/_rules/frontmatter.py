"""Rules for the frontmatter block itself (OKF v0.2 §11, §4.1)."""

from __future__ import annotations

from collections.abc import Iterable

from okf_io._rules._common import concepts, member_path
from okf_io.validate import Finding, Rule, RuleContext

CODES: tuple[str, ...] = (
    "frontmatter.missing",
    "frontmatter.unparseable",
    "frontmatter.unreadable",
    "frontmatter.missing-type",
    "frontmatter.title-recommended",
    "frontmatter.description-recommended",
)


def unreadable(ctx: RuleContext) -> Iterable[Finding]:
    """§11 rule 1, extended to a member nobody can decode.

    An addition to the rule catalog. A concept-shaped file that is not valid
    UTF-8 never reaches ``concepts`` -- the loader records it and keeps walking
    -- so without this rule it would vanish from the report entirely.
    """
    for path, why in sorted(ctx.bundle.unreadable.items()):
        yield Finding(
            "frontmatter.unreadable",
            "error",
            f"Member could not be read: {why}",
            "§11",
            path,
        )


def block_present_and_parseable(ctx: RuleContext) -> Iterable[Finding]:
    """§11 rule 1: every non-reserved `.md` carries a parseable block."""
    for concept_id, document in concepts(ctx):
        path = member_path(concept_id)
        error = document.parse_error
        if error is not None:
            yield Finding(
                "frontmatter.unparseable",
                "error",
                f"Frontmatter is present but unparseable ({error.kind}): {error.message}",
                "§11",
                path,
                error.line,
            )
        elif not document.has_frontmatter:
            yield Finding(
                "frontmatter.missing",
                "error",
                "No YAML frontmatter block",
                "§11",
                path,
            )


def required_and_recommended_keys(ctx: RuleContext) -> Iterable[Finding]:
    """§11 rule 2, plus the two §4.1 recommendations.

    Gated on the block existing and parsing. A file with no frontmatter is
    already reported by :func:`block_present_and_parseable`; adding "and it has
    no title" three more times buries that finding rather than sharpening it.
    """
    for concept_id, document in concepts(ctx):
        if document.parse_error is not None or not document.has_frontmatter:
            continue
        path = member_path(concept_id)
        frontmatter = document.fm
        if not (frontmatter.type or "").strip():
            yield Finding("frontmatter.missing-type", "error", "`type` is absent or empty", "§4.1", path)
        if not (frontmatter.title or "").strip():
            yield Finding("frontmatter.title-recommended", "warn", "No `title`", "§4.1", path)
        if not (frontmatter.description or "").strip():
            yield Finding("frontmatter.description-recommended", "warn", "No `description`", "§4.1", path)


RULES: tuple[Rule, ...] = (
    unreadable,
    block_present_and_parseable,
    required_and_recommended_keys,
)

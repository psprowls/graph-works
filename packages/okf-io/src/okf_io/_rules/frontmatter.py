"""Rules for the frontmatter block itself (OKF v0.2 §11, §4.1)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from okf_io._rules._common import concepts, member_path
from okf_io.models import value_shape
from okf_io.validate import Finding, Rule, RuleContext

CODES: tuple[str, ...] = (
    "frontmatter.missing",
    "frontmatter.unparseable",
    "frontmatter.unreadable",
    "frontmatter.missing-type",
    "frontmatter.title-recommended",
    "frontmatter.description-recommended",
    "frontmatter.value-malformed",
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


def _resolve(fm_raw: Any, path: str) -> Any:  # noqa: ANN401 -- walks a raw YAML tree of unknown shape
    """Walk *fm_raw* one dotted segment at a time; give up as soon as one fails.

    A digit segment only ever follows a list-valued key (``tags.1``,
    ``sources.0``), so it is tried as a sequence index first; every other
    segment follows a mapping. Anything that does not fit -- a missing key, an
    out-of-range index, or a scalar with more path left -- returns ``None``
    rather than guessing.
    """
    node = fm_raw
    for segment in path.split("."):
        if isinstance(node, Sequence) and not isinstance(node, str | bytes) and segment.isdigit():
            index = int(segment)
            if not 0 <= index < len(node):
                return None
            node = node[index]
        elif isinstance(node, Mapping) and segment in node:
            node = node[segment]
        else:
            return None
    return node


def coerced_values(ctx: RuleContext) -> Iterable[Finding]:
    """§11: a reserved key whose value the reader could not coerce.

    The detection already happened at parse time -- `build_frontmatter`
    recorded the dotted path in `coercion_failures` and dropped the value.
    Without this rule the record has no reader, and the whole provenance
    family can vanish from a document with a clean report.

    Fires on every entry, including paths a field rule also covers. The two
    say different things: `lifecycle.stale-after-malformed` says the value is
    not a date; this says the value is gone. Coverage elsewhere is by shape,
    not by path -- `trust.timestamp-not-iso` catches `generated.at: yesterday`
    and is silent on `generated.at: [1, 2]` -- so skipping a "covered" path
    would re-open the hole this rule closes.
    """
    for concept_id, document in concepts(ctx):
        if document.parse_error is not None or not document.has_frontmatter:
            continue
        path = member_path(concept_id)
        for field in sorted(document.fm.coercion_failures):
            raw = _resolve(document.fm_raw, field)
            shape = f" ({value_shape(raw)})" if raw is not None else ""
            yield Finding(
                "frontmatter.value-malformed",
                "warn",
                f"`{field}` is not readable as its declared type{shape}; the value was dropped",
                "§11",
                path,
            )


RULES: tuple[Rule, ...] = (
    unreadable,
    block_present_and_parseable,
    required_and_recommended_keys,
    coerced_values,
)

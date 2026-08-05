"""The rule that turns a loaded `SchemaSet` into `Finding`s over a bundle.

    validate(bundle, today=..., extra_rules=[schema_rule(schema_set)])

The only `Finding` source in this capability, and the only thing here that
touches a bundle. Nothing raises for content: a schema mismatch is a `Finding`,
never an exception.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from jsonschema.exceptions import ValidationError
from jsonschema.validators import validator_for
from okf_io import Finding, Rule, RuleContext, Severity

from okf_ext.schemas.loader import build_registry
from okf_ext.schemas.model import SchemaSet

#: The topic prefix this rule set claims. `validate()` raises the moment an
#: external rule emits a built-in prefix, and `schemas` collides with none of
#: the eight (computation, frontmatter, legacy, lifecycle, links, provenance,
#: reserved, trust). The module name is the prefix, as in okf-io.
TOPIC = "schemas"

CODES = (
    "schemas.invalid",  # frontmatter does not satisfy the schema for its type
    "schemas.no-schema-for-type",  # the loaded set has no schema for this type
)

#: Unpacked from `CODES` rather than re-typed, so a code-string edit to one
#: cannot silently drift from the other -- the habit `tags/vocabulary.py` set.
_CODE_INVALID, _CODE_NO_SCHEMA = CODES


def _sort_key(error: ValidationError) -> tuple[list[str], str]:
    """Deterministic order for one document's errors.

    `validate()` sorts findings after collection anyway, so this is about the
    rule being reproducible when called directly rather than about output order.
    """
    return ([str(part) for part in error.absolute_path], error.message)


def _where(error: ValidationError) -> str:
    """The `` at `key` `` clause, or empty when the error sits at the root.

    A `required` or `additionalProperties` error carries an empty
    `absolute_path`: the fault is the document's, not any key's. Naming a key
    there would be a guess.
    """
    if not error.absolute_path:
        return ""
    return " at `" + ".".join(str(part) for part in error.absolute_path) + "`"


def schema_rule(schema_set: SchemaSet, *, severity: Severity = "warn") -> Rule:
    """Build an `okf_io.Rule` that validates frontmatter against *schema_set*.

    **`severity` defaults to `warn`.** `Report.ok` is a claim about OKF v0.2
    conformance, and a house rule has no business making a conformant bundle
    look otherwise -- the invariant `vocabulary_rule` states. The knob exists
    because okf-io's `strict=True` does not serve this case: it promotes *every*
    warning, including the deliberately-`warn` `links.broken`, so a
    team wanting CI red on a schema violation would also get CI red on a dead
    link. Because severity is data rather than structure, this costs
    one pass-through argument and no second code namespace.

    `schemas.no-schema-for-type` is **always `warn`** regardless: it reports a
    coverage gap in the schema set, not a violation by the document.

    Frontmatter is read through `Document.fm_data(dates="iso")`, so dates arrive
    as ISO strings and `additionalProperties: false` still sees every key the
    document actually carries. Documents okf-io could not parse are skipped
    rather than re-reported.
    """
    registry = build_registry(schema_set.documents)
    validators: dict[str, Any] = {
        type_name: validator_for(dict(schema))(dict(schema), registry=registry)
        for type_name, schema in schema_set.schemas.items()
    }
    set_name = schema_set.root.name or str(schema_set.root)

    def rule(context: RuleContext) -> Iterable[Finding]:
        for concept_id in sorted(context.bundle.concepts):
            document = context.bundle.concepts[concept_id]
            if document.parse_error is not None:
                continue
            type_name = (document.fm.type or "").strip()
            if not type_name:
                continue
            path = f"{concept_id}.md"
            validator = validators.get(type_name)
            if validator is None:
                yield Finding(
                    code=_CODE_NO_SCHEMA,
                    severity="warn",
                    message=f"No schema for type `{type_name}` in `{set_name}`.",
                    spec=set_name,
                    path=path,
                    line=document.frontmatter_line("type"),
                )
                continue
            source = schema_set.sources[type_name]
            data = document.fm_data(dates="iso")
            # One finding per error, not per document: each error has its own
            # key path and therefore its own line.
            for error in sorted(validator.iter_errors(data), key=_sort_key):
                yield Finding(
                    code=_CODE_INVALID,
                    severity=severity,
                    message=f"Frontmatter fails `{source}`{_where(error)}: {error.message}.",
                    spec=source,
                    path=path,
                    line=document.frontmatter_line(*error.absolute_path),
                )

    return rule

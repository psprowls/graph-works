"""JSONSchema validation of OKF v0.2 concept frontmatter.

    from okf_ext import schemas
    schema_set = schemas.load_schemas("kb/schema")
    report = validate(bundle, today=today, extra_rules=[schemas.schema_rule(schema_set)])

Two facts, and only two, are genuinely schema-driven: a concept's frontmatter
does not satisfy the schema for its `type`, and a concept's `type` has no schema
in the loaded set. Everything else okf-schema v0.1 checked is already covered by
okf-io's own catalog.

Frontmatter is read through `Document.fm_data(dates="iso")`  and never re-parsed,
so dates arrive as ISO strings rather than as `datetime.date` objects that
both `format: date` and `type: string` misfire on.

**This module imports no sibling capability**, and never the top-level `okf_ext`
package.
"""

from __future__ import annotations

try:
    import jsonschema as _jsonschema  # noqa: F401  -- probed for its absence
except ImportError as exc:  # pragma: no cover -- exercised by a subprocess test
    raise ImportError(
        "The `schemas` capability needs `jsonschema`. Install it with `pip install 'okf-ext[schemas]'`."
    ) from exc

from okf_ext.schemas.loader import (
    DEFAULT_SCHEMA_DIRNAME,
    SCHEMA_SUFFIXES,
    build_registry,
    declared_about,
    declared_directories,
    declared_members,
    load_schemas,
)
from okf_ext.schemas.model import AboutMandate, SchemaError, SchemaSet
from okf_ext.schemas.rule import CODES, TOPIC, schema_rule

#: `schema/` is a **documented convention, not magic** -- nothing here
#: discovers it. A caller who keeps schemas inside the bundle they describe
#: splices this into `okf_io.load_bundle(root, ignore=...)`, where an ignored
#: member is "not a concept", not "not there". Two patterns because the first is
#: anchored at the start and so never matches a nested `schema/`;
#: `tags.DEFAULT_IGNORE` carries two for the same reason.
DEFAULT_IGNORE = ("schema/*", "*/schema/*")

#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase functions,
#: each group alphabetical -- `RUF022` enforces it and `test_ext_boundaries.py`
#: asserts the same invariant independently.
__all__ = [
    "CODES",
    "DEFAULT_IGNORE",
    "DEFAULT_SCHEMA_DIRNAME",
    "SCHEMA_SUFFIXES",
    "TOPIC",
    "AboutMandate",
    "SchemaError",
    "SchemaSet",
    "build_registry",
    "declared_about",
    "declared_directories",
    "declared_members",
    "load_schemas",
    "schema_rule",
]

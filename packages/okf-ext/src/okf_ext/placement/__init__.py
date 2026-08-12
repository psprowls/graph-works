"""Page placement: is this page where the bundle's own declarations say it goes?

    from okf_ext import placement
    report = validate(bundle, today=today, extra_rules=[placement.placement_rule(directories)])

Two codes, one factory, and no vocabulary of its own. `placement_rule` takes a
`{type: directory}` map -- typically `okf_ext.schemas.declared_directories(schema_set)`,
composed by the caller because a capability may not import a sibling -- plus an
optional `depth` map for two types sharing one directory.

Split from `health` along a real seam: `health.duplicate-title` is its twin in
shape, but `health_rule` takes one severity for all three of its codes, and a
caller needing `duplicate-resource` at `error` would drag `health.uncited` and
`health.log-gap` up with it.

**This module imports no sibling capability**, and never the top-level
`okf_ext` package.
"""

from __future__ import annotations

from okf_ext.placement.rule import CODES, TOPIC, placement_rule

#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase functions,
#: each group alphabetical -- `RUF022` enforces it and `test_ext_boundaries.py`
#: asserts the same invariant independently.
__all__ = [
    "CODES",
    "TOPIC",
    "placement_rule",
]

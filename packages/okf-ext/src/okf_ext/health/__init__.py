"""Bundle coherence: is this bundle internally consistent?

    from okf_ext import health
    report = validate(bundle, today=today, extra_rules=[health.health_rule()])

Two codes, one factory. The unit of work is the **whole bundle**, and the
inputs are `LinkGraph.backlinks` and `parse_log` -- which is the
seam that separates this from `render`, whose unit is one document's markdown
body and whose input is a markdown-it parse. They share no code and no data.

**Staleness is not here.** okf-io already ships `lifecycle.stale`, which fires
when a document's own `stale_after` has passed. A blanket "nothing older than N
days" policy layered on top would contradict an author who deliberately wrote
`stale_after: 2027-01-01` -- the inverse of ADR 2026-08-03-index-reconciliation's principle. A document
that declares no expiry is not stale; it is undated, which is a different fact
and not one this capability reports.

**This module imports no sibling capability**, and never the top-level
`okf_ext` package.
"""

from __future__ import annotations

from okf_ext.health.rule import CODES, TOPIC, health_rule

#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase functions,
#: each group alphabetical -- `RUF022` enforces it and `test_ext_boundaries.py`
#: asserts the same invariant independently.
__all__ = [
    "CODES",
    "TOPIC",
    "health_rule",
]

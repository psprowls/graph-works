"""Regeneration: how a machine rewrites a document a human also edits.

    from okf_ext import shape, generators

    section_set = shape.load_sections(bundle.root / "_sections")
    render = generators.Render(
        frontmatter={"title": "okf-io", "sources": [...]},
        sections={"Sources": "- [[a]]\\n- [[b]]"},
    )
    plan = generators.plan_regenerate(bundle, section_set, {"entities/okf-io": render})
    result = generators.apply(bundle, plan)

Two halves, one answer. **Key-level frontmatter ownership** declares which
keys a generator may claim, so a re-run rewrites `sources` and never touches
`status`. **Section ownership** declares who owns each H2, so a re-run
rewrites `## Sources` and carries `## How this synthesis has changed` through
verbatim. Neither is useful alone: a generator that owns the right keys and
flattens the prose has still destroyed the page.

**The capability owns the merge, never the render.** What a `## Sources` list
should say depends on a code graph this package cannot see. What may be
written into the document, and how it lands without disturbing a neighbouring
byte, is this package's question.

**No lane vocabulary ships.** There is no `ENTITY_KEYS` and no Diátaxis
ownership map, for the reason there is no `PLAN_TABLE` in `tables` and no
`FEATURE_SECTIONS` in `sections`.

**No rule ships.** No `TOPIC`, no `CODES`, no `Finding` -- this is a
primitive, exactly as `tables` is. The seeding axis is already covered:
`sections.missing` fires for a required section that is not there and
`sections.unfilled` for one that is empty, both reading the same declaration
this capability reads.

**No new dependency.** Nothing here needs anything `okf-ext` does not already
declare unconditionally, so there is no extra and no `ImportError` guard --
the third such capability after `search` and `sections`.

**This module imports no sibling capability**, and never the top-level
`okf_ext` package. The declaration types come from `okf_ext.shape`.
"""

from __future__ import annotations

from okf_ext.generators.apply import apply
from okf_ext.generators.frontmatter import key_edits
from okf_ext.generators.model import KeyEdit, Regeneration, RegenerationPlan, Render, SectionEdit
from okf_ext.generators.plan import plan_regenerate
from okf_ext.generators.regenerate import regenerate_body
from okf_ext.writing import ApplyResult, FailureKind, Skipped, SkipReason, WriteFailure

#: Ordered UPPER_SNAKE_CASE constants, then CapWords classes, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this, and
#: `test_ext_boundaries.py` asserts the same invariant independently.
__all__ = [
    "ApplyResult",
    "FailureKind",
    "KeyEdit",
    "Regeneration",
    "RegenerationPlan",
    "Render",
    "SectionEdit",
    "SkipReason",
    "Skipped",
    "WriteFailure",
    "apply",
    "key_edits",
    "plan_regenerate",
    "regenerate_body",
]

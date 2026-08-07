"""Body-shape declarations: what sections a concept of a given type carries.

    from okf_ext import shape, sections
    section_set = shape.load_sections("kb/_sections")
    report = validate(bundle, today=today, extra_rules=[sections.section_rule(section_set)])
    plan = sections.plan_sections(bundle, section_set)

New code should import the declaration surface (`load_sections`,
`SectionSet`, `SectionSpec`, `TypeSections`, `SectionError`,
`DEFAULT_SECTIONS_DIRNAME`, `DEFAULT_IGNORE`, `SECTION_SUFFIXES`) from
`okf_ext.shape`, as above -- see the README's graduation recipe. This module
still re-exports the same objects under the old names for one minor version
so nothing importing `okf_ext.sections.load_sections` today breaks; that
path keeps working, it is just not the one to teach.

Content-schema validation and item templates are one thing, not two. "This
required section is missing" and "create this required section from its
placeholder" are the same declaration answered by a rule and by a writer.
Keying both off one declaration is the move `okf_io.migrate` makes with
`doc.fm.fallbacks`: reader, validator and writer cannot disagree about what
counts, because there is only one place it is written down.

`schemas` validates frontmatter and stops there -- JSONSchema cannot express
"the body carries an H2 called `## Plan`" -- which is why body shape had no
home before this.

**No lane vocabulary ships.** There is no `FEATURE_SECTIONS` constant and no
Diátaxis skeleton here, for the same reason there is no `PLAN_TABLE` in
`tables`: shipping one lane's section names from tier 2 would put every
adopting bundle into a permanent finding state for a vocabulary it never
adopted.

**No new dependency.** The heading walk is shared `okf_ext.body`, the write
engine shared `okf_ext.writing`, the line primitives shared `okf_ext.splice`,
and `ruamel.yaml` is already unconditional -- so there is no extra and no
`ImportError` guard in this module, following `search` rather than `schemas`.

**This module imports no sibling capability**, and never the top-level
`okf_ext` package.

**The declaration types and their loader live in `okf_ext.shape`.** They are
re-exported here for one minor version -- the README's graduation recipe --
because `okf_ext.generators` reads the same declaration and a capability may
not import a sibling.
"""

from __future__ import annotations

from okf_ext.sections.model import SectionInsert, SectionPlan, SectionSplice
from okf_ext.sections.rule import CODES, TOPIC, section_rule
from okf_ext.sections.scaffold import apply, plan_sections, render_skeleton
from okf_ext.shape import (
    DEFAULT_IGNORE,
    DEFAULT_SECTIONS_DIRNAME,
    SECTION_SUFFIXES,
    SectionError,
    SectionSet,
    SectionSpec,
    TypeSections,
    load_sections,
)
from okf_ext.writing import ApplyResult, FailureKind, Skipped, SkipReason, WriteFailure

#: Ordered UPPER_SNAKE_CASE constants, then CapWords classes, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this, and
#: `test_ext_boundaries.py` asserts the same invariant independently.
__all__ = [
    "CODES",
    "DEFAULT_IGNORE",
    "DEFAULT_SECTIONS_DIRNAME",
    "SECTION_SUFFIXES",
    "TOPIC",
    "ApplyResult",
    "FailureKind",
    "SectionError",
    "SectionInsert",
    "SectionPlan",
    "SectionSet",
    "SectionSpec",
    "SectionSplice",
    "SkipReason",
    "Skipped",
    "TypeSections",
    "WriteFailure",
    "apply",
    "load_sections",
    "plan_sections",
    "render_skeleton",
    "section_rule",
]

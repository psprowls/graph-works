"""Body-shape declarations: what sections a concept of a given type carries.

    from okf_ext import sections
    section_set = sections.load_sections("kb/_sections")
    report = validate(bundle, today=today, extra_rules=[sections.section_rule(section_set)])
    plan = sections.plan_sections(bundle, section_set)

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
"""

from __future__ import annotations

from okf_ext.sections.loader import (
    DEFAULT_SECTIONS_DIRNAME,
    SECTION_SUFFIXES,
    load_sections,
)
from okf_ext.sections.model import (
    SectionError,
    SectionInsert,
    SectionPlan,
    SectionSet,
    SectionSpec,
    SectionSplice,
    TypeSections,
)
from okf_ext.sections.rule import CODES, TOPIC, section_rule
from okf_ext.sections.scaffold import apply, plan_sections, render_skeleton
from okf_ext.writing import ApplyResult, FailureKind, Skipped, SkipReason, WriteFailure

#: `_sections/` is a **documented convention, not magic** -- nothing here
#: discovers it. A caller who keeps declarations inside the bundle they
#: describe splices this into `okf_io.load_bundle(root, ignore=...)`, where an
#: ignored member is "not a concept", not "not there". Two patterns because
#: the first is anchored at the start and so never matches a nested
#: `_sections/`; `tags.DEFAULT_IGNORE` and `schemas.DEFAULT_IGNORE` each carry
#: two for the same reason.
DEFAULT_IGNORE = ("_sections/*", "*/_sections/*")

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

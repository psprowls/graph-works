"""Tolerant markdown table read, and an idempotent row splice.

    from okf_ext import tables
    spec = tables.TableSpec(columns=(tables.Column("action"), tables.Column("done when")))
    read = tables.read_section(doc.body, "Plan", spec)

Three unrelated surveys asked for the same primitive: `wiki-io` §8 named
`parse_markdown_table` and `find_section` as "the parts with no okf-io
equivalent", `work-io` §7.4 called it the second request, and the Diátaxis
ingestion-layer survey §8.2 the third -- because Reference mode *is* a table
discipline, and the `diataxis.no-entries` gate cannot be written without a
table read.

Two read levels, because the three consumers want different things. `read` /
`read_all` know nothing and return raw cells, which is what the file-map walk
and the Diátaxis gate need. `read_section` layers a `TableSpec` -- synonyms,
canonical names, and the four-state result -- on top, which is what the work
lane's plan tables need. The states are meaningful only relative to a spec,
so they live at the spec level and the generic level stays honest about
knowing nothing.

**This module exports no `TOPIC` and no `CODES`.** It emits no `Finding`: it
is the primitive lint rules are built on, not a rule itself.

**This module imports the shared `okf_ext.body` and `okf_ext.writing`
layers and nothing else from its own package.** It never imports a sibling
capability, and never `okf_ext` itself.
"""

from __future__ import annotations

from okf_ext.tables.model import (
    Column,
    ReadState,
    RowSplice,
    SectionRead,
    SpliceAction,
    SplicePlan,
    Table,
    TableSpec,
    TextSplice,
)
from okf_ext.tables.read import read, read_all, read_section
from okf_ext.tables.splice import apply, plan_row, splice_text
from okf_ext.writing import ApplyResult, FailureKind, Skipped, SkipReason, WriteFailure

#: Ordered UPPER_SNAKE_CASE constants, then CapWords classes, then lowercase
#: functions, each group alphabetical -- `ruff`'s `RUF022` enforces exactly
#: this grouping via `just lint`, and `test_ext_boundaries.py` checks the same
#: invariant so a stale ordering fails the test suite too.
__all__ = [
    "ApplyResult",
    "Column",
    "FailureKind",
    "ReadState",
    "RowSplice",
    "SectionRead",
    "SkipReason",
    "Skipped",
    "SpliceAction",
    "SplicePlan",
    "Table",
    "TableSpec",
    "TextSplice",
    "WriteFailure",
    "apply",
    "plan_row",
    "read",
    "read_all",
    "read_section",
    "splice_text",
]

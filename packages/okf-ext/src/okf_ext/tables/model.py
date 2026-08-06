"""Frozen values the tables capability returns.

All frozen and slotted, matching the core. Nothing here holds a live
exception or a mutable container, so every value survives `json.dumps` --
the habit `fm_data(dates="iso")` and `Severity`-as-`Literal` established in
okf-io.

This module imports the shared layer and nothing else from its own package.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from okf_ext.writing import Skipped

#: The four states a spec-driven read distinguishes. They are meaningful only
#: relative to a spec -- `malformed` means *these headers were not found* --
#: which is why the generic read has no state at all.
ReadState = Literal["missing", "malformed", "empty", "ok"]

#: What a splice did. `None` on a `TextSplice` means nothing changed.
SpliceAction = Literal["append", "update", "create-table", "create-section"]

__all__ = [
    "Column",
    "ReadState",
    "RowSplice",
    "SectionRead",
    "SpliceAction",
    "SplicePlan",
    "Table",
    "TableSpec",
    "TextSplice",
]


@dataclass(frozen=True, slots=True)
class Table:
    """One pipe table, exactly as written. 1-based, body-relative.

    `stop` is the last line of the **contiguous table block** -- which is the
    last data row whenever there is one, and the delimiter (or the header)
    when there is not. §5.2 of the spec says "the last data row", which is
    undefined for a table with none; the splice needs "the last table line"
    to append after, and the two agree in every case the spec defines.

    `delimiter` is `0` when there is no `|---|---|` row. Tolerance is the
    point: its absence is reported, not fatal.

    A row shorter than the header is padded with `""`. A row longer than the
    header keeps its extra cells -- dropping them would silently discard
    content a caller may be about to rewrite.
    """

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    start: int  # the header row
    stop: int  # inclusive: the last line of the table
    delimiter: int  # the `|---|---|` row, or 0 when absent


@dataclass(frozen=True, slots=True)
class Column:
    """One expected column: its canonical name, and what else it may be called."""

    name: str  # canonical
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TableSpec:
    """What a caller expects a table to carry.

    **Data a caller constructs, never a constant this package ships.** No
    `PLAN_TABLE` or `FILE_MAP` spec lives here: shipping one lane's column
    names in a bundle-agnostic package is the same hazard the `rules` item
    names for lifecycle rules.

    `min_matched` defaults to 2, which is `work_io.parse_plan`'s threshold: a
    three-column plan table missing one column is still a plan table; a table
    sharing one word with the spec is not. It is clamped to the number of
    columns, so a single-column spec is not permanently `malformed` by a
    default it could never meet.
    """

    columns: tuple[Column, ...]
    min_matched: int = 2


@dataclass(frozen=True, slots=True)
class SectionRead:
    """A spec-driven read of one section's table.

    `rows` maps canonical names to cells. It **omits columns the header did
    not carry**, so `list(row)` tells a caller what was really there -- but a
    lookup of any other column in the spec reads `""` rather than raising, so
    a consumer walking a partially-conformant corpus does not have to guard
    every access. `matched` is what a lint rule reports when it wants to say
    *which* column is absent.
    """

    state: ReadState
    rows: tuple[Mapping[str, str], ...]
    table: Table | None
    matched: tuple[str, ...]  # canonical names found in the header, spec order


@dataclass(frozen=True, slots=True)
class TextSplice:
    """The result of a pure text->text splice.

    `before` / `after` / `changed` is deliberately okf-io's writer
    vocabulary, shared by `IndexUpdate`, `LogAppend` and `Migration`. There is
    no `dry_run` flag: nothing in `splice_text` touches a filesystem, so a
    flag pretending otherwise would be a lie.

    `action is None` exactly when nothing changed, and `line` is `0` then.
    Otherwise `line` is the first line of the whole inserted or rewritten
    block -- the new row for `append`, the rewritten row for `update`, the
    header row for `create-table`, the heading for `create-section` **or the
    blank line separating it from prior content, when one was inserted**.

    One line above `line` can still change, in one narrow case: when the
    insertion point is one past a body with no trailing terminator, the
    previously-last line gains one (`"x"` becomes `"x\n"`) so the new content
    does not run onto it. That line is not part of what the splice *claims*
    -- it is forced by the body's own trailing-newline state, differs from
    the original by exactly a terminator and never by its text, and (because
    the insertion is at the true end) nothing follows it that could hide a
    second, unrelated change. Every other line at or below `line` is
    reachable only through the row or block the action names.
    """

    before: str
    after: str
    action: SpliceAction | None
    line: int

    @property
    def changed(self) -> bool:
        return self.before != self.after


@dataclass(frozen=True, slots=True)
class RowSplice:
    """One document's splice, ready to write.

    `after` is the whole new body. §5.5 of the spec lists only `row`,
    `action`, `line` and `digest`, which together do not determine the new
    body without also carrying the spec and the options -- so `apply` could
    not reproduce the write from them. Storing the rendered body instead
    makes `apply` a pure writer (digest-check, then write), with no chance of
    the plan and the apply disagreeing about what a row insert means.

    `digest` is a sha256 of the body the splice was computed against. A
    digest rather than a re-derived state comparison, because the splice is a
    deterministic function of the *whole* body: a change anywhere in it can
    move the line the row lands on, so "the section still parses to the same
    state" is a weaker check than it looks.
    """

    concept_id: str
    path: str  # bundle-relative posix
    heading: str
    row: Mapping[str, str]
    action: SpliceAction
    line: int
    digest: str
    after: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SplicePlan:
    """A preview you can inspect and filter before anything is written.

    A value rather than a `dry_run=True` flag, for the reason `RenamePlan`
    already gives: "apply 38 of these 40" is a thing a boolean cannot
    express. **Idempotence surfaces as an empty plan** -- a row already
    present produces no splice at all, and `splices` names exactly what would
    change.
    """

    root: Path  # the bundle this was planned against
    splices: tuple[RowSplice, ...]
    skipped: tuple[Skipped, ...]

    @property
    def is_empty(self) -> bool:
        return not self.splices

    @property
    def concept_ids(self) -> tuple[str, ...]:
        return tuple(sorted({splice.concept_id for splice in self.splices}))

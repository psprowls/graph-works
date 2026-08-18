"""Rules for the `## Plan` table.

`PLAN_TABLE_SPEC` lives here and is re-exported from `work_tracker_okf.rules`,
so child 6 and the child-2 tests that build it inline have one place to read it
from. `okf_ext.tables` deliberately ships no lane's column names -- a
`TableSpec` is data a caller constructs -- so this is where the lane constructs
its own, once.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from okf_ext.tables import Column, TableSpec, read_section
from okf_io import Finding, Rule, RuleContext, Severity

from work_tracker_okf._rules._common import LaneConfig, with_documents
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import PARENT_TYPES

CODES: tuple[str, ...] = (
    "plan.accepted-without-plan",
    "plan.table-malformed",
    "plan.done-when-missing",
    "plan.action-target-missing",
)

_SPEC = "work_tracker_okf._rules.plan"

#: The heading all six sections declarations require.
_HEADING = "Plan"

#: The columns `assets/_sections/_fragments.work_tracker.yaml` declares.
PLAN_TABLE_SPEC = TableSpec(columns=(Column("action"), Column("done when"), Column("rationale")))

#: work-io's token scanner: an `a/b`-shaped whitespace-delimited word. Matched
#: against one `str.split()` token at a time (see `_actions`), never against
#: the whole cell -- a `\b`-anchored search over the raw cell text lets a
#: `https://` scheme's `://` slide past the boundary check and misdetects a URL
#: as a bare path.
_PATH_RE = re.compile(r"[\w][\w.\-]*/[\w.\-/]+")


def _finding(code: str, severity: Severity, item: WorkItem, message: str) -> Finding:
    return Finding(code=code, severity=severity, message=message, spec=_SPEC, path=item.path, line=None)


def table(ctx: RuleContext) -> Iterable[Finding]:
    """4, 17, 15: the three codes a single `read_section` answers.

    The fourth state, `missing`, gets **no lane code**: `## Plan` is
    `required: true` in all six sections declarations, so `sections.missing` from
    tier 2 already reports it, and a second code for the same fact is the drift
    child 1 was avoiding when it moved "accepted with an empty plan" down here.

    `accepted-without-plan` accepts both `ok` and `empty` -- work-io's behaviour
    exactly. An accepted item with a seeded-but-unfilled table is filed
    correctly; it is a malformed or absent one that is the finding.

    One consequence of the `ok` gate on `done-when-missing` worth naming: a table
    that matched two of three spec columns reads `ok`, and if `Done when` is the
    unmatched one, every row reads `""` and the rule fires on all of them. That
    is the right report -- the column really is not there -- and
    `SectionRead.matched` is what a future message could name.
    """
    for item, document in with_documents(ctx):
        read = read_section(document.body, _HEADING, PLAN_TABLE_SPEC)
        if item.workflow_status == "accepted" and read.state not in ("ok", "empty"):
            yield _finding(
                "plan.accepted-without-plan",
                "error",
                item,
                f"`workflow_status: accepted` but the `## {_HEADING}` table is {read.state}",
            )
        if read.state == "malformed":
            yield _finding(
                "plan.table-malformed",
                "warn",
                item,
                f"`## {_HEADING}` is present but carries no table matching its declared columns",
            )
        if item.type in PARENT_TYPES and read.state == "ok" and any(not row["done when"] for row in read.rows):
            yield _finding(
                "plan.done-when-missing",
                "warn",
                item,
                f"`type: {item.type}` plan has rows with an empty `Done when` cell",
            )


def _actions(repo_root: Path) -> Rule:
    """11: an action cell naming a repo path that does not exist.

    Only rows of an `ok` table are scanned, and only the `Action` column --
    `Done when` and `Rationale` are prose. `http`-prefixed tokens are skipped.
    work-io's `workspace_root` second chance is **not** ported: nothing in this
    lane reads a path it was not handed, and one injected root is the whole
    contract.
    """

    def rule(ctx: RuleContext) -> Iterable[Finding]:
        for item, document in with_documents(ctx):
            read = read_section(document.body, _HEADING, PLAN_TABLE_SPEC)
            if read.state != "ok":
                continue
            for row in read.rows:
                for word in row["action"].split():
                    if word.startswith("http"):
                        continue
                    match = _PATH_RE.fullmatch(word.strip(".,;:()"))
                    if match is None:
                        continue
                    token = match.group()
                    if (repo_root / token).exists():
                        continue
                    yield _finding(
                        "plan.action-target-missing",
                        "error",
                        item,
                        f"plan action names `{token}`, which does not exist under the repo root",
                    )

    return rule


def rules(config: LaneConfig) -> tuple[Rule, ...]:
    """`repo_root=None` **skips** `plan.action-target-missing` rather than
    reporting it as a failure: not knowing where the repo is says nothing about
    whether the paths are good. work-io's behaviour, and the right one."""
    if config.repo_root is None:
        return (table,)
    return (table, _actions(config.repo_root))

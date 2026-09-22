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

#: The columns `assets/sections/_fragments.work_tracker.yaml` declares.
PLAN_TABLE_SPEC = TableSpec(columns=(Column("action"), Column("done when"), Column("rationale")))

#: work-io's token scanner, widened by one thing: an optional leading `/`, since
#: OKF root-absolute links (`/work/<item>/references/02-plan.md`, the boilerplate
#: "Execute implementation plan: ..." row's own spelling) are exactly as common a
#: plan-action shape as the bare `a/b` one work-io scans for. Matched against one
#: `str.split()` token at a time (see `_actions`), never against the whole cell --
#: a `\b`-anchored search over the raw cell text lets a `https://` scheme's `://`
#: slide past the boundary check and misdetects a URL as a bare path.
_PATH_RE = re.compile(r"/?[\w][\w.\-]*/[\w.\-/]+")


def _finding(code: str, severity: Severity, item: WorkItem, message: str) -> Finding:
    return Finding(code=code, severity=severity, message=message, spec=_SPEC, path=item.page_path, line=None)


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
        if item.work_status == "accepted" and read.state not in ("ok", "empty"):
            yield _finding(
                "plan.accepted-without-plan",
                "error",
                item,
                f"`work_status: accepted` but the `## {_HEADING}` table is {read.state}",
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


def _actions(repo_roots: tuple[Path, ...], vault_root: Path | None) -> Rule:
    """11: an action cell naming a path that exists under neither root.

    Only rows of an `ok` table are scanned, and only the `Action` column --
    `Done when` and `Rationale` are prose. `http`-prefixed tokens are skipped.
    work-io's `workspace_root` second chance is **not** ported: nothing in this
    lane reads a path it was not handed, and one injected root is the whole
    contract.

    **`*vault_root*` is the bundle root** (`WorkspaceLayout.bundle_dir`, the
    `okf/` directory this vault's own root-absolute links resolve against --
    AGENTS.md's "cite with root-absolute markdown links" convention), not the
    workspace root a sibling `.gw/` and `scratch/` also live under. Every
    caller composing this rule passes the same value, so `gw wiki lint`,
    `gw work lint`, and the mutation gate agree on one finding.

    A bare token ("Edit packages/foo/bar.py") is checked against **either**
    configured root, not just one: a hand-written action naming a code file
    resolves under a repo root, while the one boilerplate "Execute
    implementation plan: ..." row every plan-stage item carries can also name
    its own plan artifact bare, which resolves under *vault_root*. In a
    co-located topology the two roots are the same directory and this
    collapses to a single check; in a split topology (workspace and code repo
    are different git repos) they are not, and a token existing under either
    is enough. *repo_roots* holds every declared code repository -- one in
    the common case, several in a multi-repository workspace -- and a token
    under any one of them is enough too.

    **A root-absolute token (`/work/...`) is checked only against
    *vault_root***, never against a repo root: OKF's own convention gives it
    one unambiguous meaning, a bundle-relative path, so checking it against a
    code repository would be answering a question this token was never
    asking. It is skipped, not flagged, when *vault_root* is `None` -- not
    knowing where the vault root is says nothing about whether the path is
    good, the same reasoning that skips the whole rule when no root at all
    is configured.
    """

    repo_name = "the repo root" if len(repo_roots) <= 1 else "any repo root"
    bare_root_names = (
        " or ".join(
            name
            for name, present in ((repo_name, bool(repo_roots)), ("the vault root", vault_root is not None))
            if present
        )
        or "the repo root"
    )

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
                    if token.startswith("/"):
                        if vault_root is None or (vault_root / token[1:]).exists():
                            continue
                        yield _finding(
                            "plan.action-target-missing",
                            "error",
                            item,
                            f"plan action names `{token}`, which does not exist under the vault root",
                        )
                        continue
                    if any((root / token).exists() for root in repo_roots):
                        continue
                    if vault_root is not None and (vault_root / token).exists():
                        continue
                    yield _finding(
                        "plan.action-target-missing",
                        "error",
                        item,
                        f"plan action names `{token}`, which does not exist under {bare_root_names}",
                    )

    return rule


def rules(config: LaneConfig) -> tuple[Rule, ...]:
    """No root at all **skips** `plan.action-target-missing` rather than
    reporting it as a failure: not knowing where a root is says nothing about
    whether the paths under it are good. work-io's behaviour, and the right
    one."""
    if not config.code_roots and config.vault_root is None:
        return (table,)
    return (table, _actions(config.code_roots, config.vault_root))

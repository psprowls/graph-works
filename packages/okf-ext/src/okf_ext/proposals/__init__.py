"""Proposals -- how a generator says "this page should exist" without being
allowed to create it.

    from datetime import UTC, datetime
    from okf_ext import proposals

    now = datetime.now(UTC)                      # the caller owns the clock
    plan = proposals.plan_propose(
        bundle,
        "pages/claude-vs-bedrock-ingest-idiom.md",
        [{"id": "src-ingest", "resource": "/sources/ingest.md", "title": "Ingest guidance"}],
        title="Claude vs Bedrock ingest idiom",
        description="Two sources argue for one page.",
        by="agent:ingest",
        at=now,
    )
    proposals.apply(bundle, plan)

Three transitions and two doors. A proposal is **filed** (`plan_propose`,
an upsert on its target), **decided** (`plan_decide`), and **promoted**
(`plan_promote`). `plan_create` is the second door onto the same writer, for a
page a human asked for directly. Everything is plan/apply: a plan is
inspectable and inert until `apply` writes it.

**Identity is the target path**, because a member's id in OKF is its path. Two
proposals for one page are one proposal and merge. Reading goes by `type:
Proposal` plus `target`, never by file path.

**Provenance is native `sources[]`**, not a capability-owned `origins[]`
dialect, and a decision is OKF §5.2's `verified` event rather than an invented
`decided:` key. Promotion copies `sources[]` verbatim, which is why `moves`
already repairs it and search and trust already read it.

**No lane vocabulary ships.** The target page's `type`, its body skeleton and
mode discipline, archive policy and any staleness rule over the backlog all
require vocabulary this tier cannot have -- they belong to the caller, for the
reason there is no `PLAN_TABLE` in `tables` and no `ENTITY_KEYS` in
`generators`.

**No rule ships.** No `TOPIC`, no `CODES`, no `Finding` -- a primitive, exactly
as `tables` and `generators` are. `page_status` is an extension key and a
proposal carries no OKF `status`, so adopting the capability adds no finding to
any bundle.

**No new dependency.** The fourth such capability after `search`, `sections`
and `generators`.

**This module imports no sibling capability**, and never the top-level
`okf_ext` package.
"""

from __future__ import annotations

from okf_ext.proposals.apply import apply
from okf_ext.proposals.model import (
    OWNED_PROVENANCE_KEYS,
    PAGE_STATUSES,
    PROPOSAL_TYPE,
    Decision,
    DecisionPlan,
    Mode,
    PagePlan,
    PageRender,
    PageStatus,
    Plan,
    Proposal,
    ProposalPlan,
    Refusal,
    RefusalKind,
    Write,
)
from okf_ext.proposals.plan import (
    list_proposals,
    mode,
    placement,
    plan_create,
    plan_decide,
    plan_promote,
    plan_propose,
)
from okf_ext.proposals.render import HEADER, BodyRenderer, render_body
from okf_ext.writing import ApplyResult, FailureKind, Skipped, SkipReason, WriteFailure

#: Ordered UPPER_SNAKE_CASE constants, then CapWords classes, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this, and
#: `test_ext_boundaries.py` asserts the same invariant independently.
__all__ = [
    "HEADER",
    "OWNED_PROVENANCE_KEYS",
    "PAGE_STATUSES",
    "PROPOSAL_TYPE",
    "ApplyResult",
    "BodyRenderer",
    "Decision",
    "DecisionPlan",
    "FailureKind",
    "Mode",
    "PagePlan",
    "PageRender",
    "PageStatus",
    "Plan",
    "Proposal",
    "ProposalPlan",
    "Refusal",
    "RefusalKind",
    "SkipReason",
    "Skipped",
    "Write",
    "WriteFailure",
    "apply",
    "list_proposals",
    "mode",
    "placement",
    "plan_create",
    "plan_decide",
    "plan_promote",
    "plan_propose",
    "render_body",
]

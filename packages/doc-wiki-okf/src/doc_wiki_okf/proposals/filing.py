"""File one proposal: resolve the type, build the renderer, hand off the merge.

Only the composition lives here, and it is short because the capability does
the work: argument parsing is `doc_wiki_okf.cli`'s, `type` validation is the
pool's own `PoolError`, and slugification is `ProposalPool.target_for`'s.

**It writes nothing.** A plan is inspectable and inert until the caller applies
it, which is `okf_ext.proposals`' posture and this inherits it rather than
adding a `dry_run` flag.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from okf_ext.proposals import ProposalPlan, plan_propose
from okf_io import Bundle

from doc_wiki_okf.proposals.pool import ProposalPool
from doc_wiki_okf.proposals.render import ReviewRenderer


def plan_file(
    bundle: Bundle,
    pool: ProposalPool,
    *,
    type_name: str,
    title: str,
    description: str,
    source: Mapping[str, Any],
    by: str,
    at: datetime,
) -> ProposalPlan:
    """Plan filing one source's argument for a page of *type_name*.

    The target is the **undated** one: it is the proposal's identity, so it
    must not move when the calendar does, and a re-fired source has to merge
    rather than fork. The date is assigned at promotion. The type is recorded
    as `target_type`, because a directory may host several types.

    Raises `PoolError` (a `KeyError`) for a type the pool cannot file into --
    locked, refused, or unknown -- naming which. *type_name* is matched
    case-insensitively and recorded in its declared spelling.

    The target is resolved through `bundle.member_id`, so it tracks the raw
    disk id rather than the slugified title alone (see the history in git
    for why that coupling is safe today).
    """
    entry = pool[type_name]
    target = pool.target_for(entry.name, title)
    raw_target = bundle.member_id(target)
    resolved = raw_target if raw_target is not None else target
    render = ReviewRenderer(
        type_name=entry.name, target=resolved, mode="update" if raw_target is not None else "create"
    )
    return plan_propose(
        bundle,
        resolved,
        [dict(source)],
        title=title,
        description=description,
        by=by,
        at=at,
        render=render,
        target_type=entry.name,
    )


__all__ = ["plan_file"]

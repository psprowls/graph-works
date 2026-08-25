"""File one proposal: resolve the lane, build the renderer, hand off the merge.

The replacement for `wiki_io/file_proposal.py`. Of its 90 lines, the argparse
shim became `doc_wiki_okf.cli`, the `kind` validation became the lane lookup's
own `KeyError`, and the `slugify` call moved into `LaneSet.target_for`. What is
left is the composition, and it is short because the capability does the work.

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

from doc_wiki_okf.proposals.lanes import LaneSet
from doc_wiki_okf.proposals.render import ReviewRenderer


def plan_file(
    bundle: Bundle,
    lane_set: LaneSet,
    *,
    lane: str,
    title: str,
    description: str,
    source: Mapping[str, Any],
    by: str,
    at: datetime,
) -> ProposalPlan:
    """Plan filing one source's argument for a page in *lane*.

    The target is the **undated** one: it is the proposal's identity, so it
    must not move when the calendar does, and a re-fired source has to merge
    rather than fork. The date is assigned at promotion.

    Raises `KeyError` for a lane the set does not declare -- caller
    configuration, and the one validation `file_proposal.py` did that survives.

    The target is resolved through `bundle.member_id`, so it now also tracks
    the raw disk id rather than the slugified title alone. Real slugs are
    always ASCII, so this is unreachable today, but it is a new coupling: if
    the same page were re-saved under a different Unicode normalization
    between two filings for the same source, a second filing could compute a
    different target and fork instead of merging.
    """
    target = lane_set.target_for(lane, title)
    raw_target = bundle.member_id(target)
    resolved = raw_target if raw_target is not None else target
    render = ReviewRenderer(
        lane=lane_set[lane],
        target=resolved,
        mode="update" if raw_target is not None else "create",
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
    )


__all__ = ["plan_file"]

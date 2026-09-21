"""Promote an approved proposal: stamp the date, write the page, retarget the ledger.

**Three steps, one plan.** `plan_promote` already makes the page write and the
`page_status` flip inseparable; this adds the retarget to the same flip rather
than writing it first, so all three commit together or not at all. Splitting the
retarget out would hand back exactly the "two writes that can drift" property
the capability exists to remove.

Retargeting after approval is safe because approval closes the merge: a decided
proposal refuses further `plan_propose` calls with `already-decided`, so identity
cannot shift under a merge still in flight. The page write is already ordered
before the flip inside `plan_promote`, so a partial failure leaves a proposal
`approved` rather than a ledger claiming `created` for a page that never landed.

**The page lands scaffolded and empty.** Frontmatter is `title` and
`description` only, and the body is the type's declared section skeleton -- the
capability writes a page's existence and provenance, never its prose.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

from okf_ext.proposals import PagePlan, PageRender, Proposal, Refusal, plan_promote
from okf_ext.sections import render_skeleton
from okf_ext.shape import SectionSet
from okf_io import Bundle

from doc_wiki_okf.proposals.lanes import Lane, LaneSet


def page_render(lane: Lane, proposal: Proposal, *, section_set: SectionSet, on: date | None = None) -> PageRender:
    """The page this promotion writes: the lane's type, the declared skeleton,
    and the proposal's own title and description. For the ADR lane, given *on*, it
    also carries `decision_date` and `status: stable`, which the `Adr` schema needs.

    Nothing in `OWNED_PROVENANCE_KEYS` -- `generated`, `sources` and `verified`
    are the capability's, and `plan_create` raises on a caller that supplies
    one. Exposed rather than inlined so a test can assert that directly rather
    than inferring it from the absence of an exception.

    Raises `KeyError` when *section_set* does not declare the lane's type --
    caller configuration, as in `diataxis.pages.new_page_text`.
    """
    frontmatter: dict[str, object] = {"title": proposal.title, "description": proposal.description}
    if lane.name == "adr" and on is not None:
        # The `Adr` schema requires `decision_date`. A promotion is a human
        # decision taken on *on*, so the ADR is `stable` from the day it lands.
        frontmatter["decision_date"] = on.isoformat()
        frontmatter["status"] = "stable"
    return PageRender(
        type=lane.type_name,
        body=render_skeleton(section_set.types[lane.type_name]),
        frontmatter=frontmatter,
    )


def plan_promotion(
    bundle: Bundle,
    lane_set: LaneSet,
    proposal: Proposal,
    *,
    section_set: SectionSet,
    by: str,
    at: datetime,
    on: date,
) -> PagePlan:
    """Plan promoting *proposal* into its dated page.

    The lane is recovered from the proposal's current target by directory, the
    same lookup `proposal show` uses to address a proposal by path alone. A
    target in no declared directory is a refusal, not a guess.
    """
    lane = lane_set.lane_for(proposal.target)
    if lane is None:
        return PagePlan(
            root=bundle.root,
            target=proposal.target,
            mode="create",
            proposal=proposal.member,
            writes=(),
            refusals=(
                Refusal(
                    path=proposal.member,
                    kind="malformed-proposal",
                    detail=(
                        f"target {proposal.target!r} is in none of the declared lane directories "
                        f"({', '.join(declared.directory for declared in lane_set.lanes)}); "
                        f"this layer resolves a lane by directory and will not guess one"
                    ),
                ),
            ),
        )

    dated = lane_set.target_for(lane.name, proposal.title, on=on)
    plan = plan_promote(
        bundle,
        replace(proposal, target=dated),
        page_render(lane, proposal, section_set=section_set, on=on),
        by=by,
        at=at,
    )
    return _retargeted(plan, proposal.member, dated)


def _retargeted(plan: PagePlan, member: str, dated: str) -> PagePlan:
    """*plan* with the ledger flip also carrying the new `target`.

    A `dataclasses.replace` on one `Write` inside the returned tuple, rather
    than a second plan: the retarget has to land with the flip, and this is the
    smallest edit that makes it inseparable from it.
    """
    writes = tuple(
        replace(write, frontmatter={**write.frontmatter, "target": dated})
        if write.member == member and "page_status" in write.frontmatter
        else write
        for write in plan.writes
    )
    return replace(plan, writes=writes)


__all__ = ["page_render", "plan_promotion"]

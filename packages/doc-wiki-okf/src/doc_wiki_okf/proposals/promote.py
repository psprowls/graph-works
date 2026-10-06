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

Frontmatter is `title`, `description`, and the type's own promotion block; the
body is the type's declared section skeleton.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

from okf_ext.proposals import PagePlan, PageRender, Proposal, Refusal, plan_promote
from okf_ext.proposals import mode as target_mode
from okf_ext.schemas import ProposableType
from okf_ext.sections import render_skeleton
from okf_ext.shape import SectionSet
from okf_io import Bundle

from doc_wiki_okf.proposals.pool import ProposalPool


def page_render(
    entry: ProposableType, proposal: Proposal, *, section_set: SectionSet, on: date | None = None
) -> PageRender:
    """Render the type's skeleton, title, description and promotion frontmatter.

    Given *on*, expand the promotion block's `{on}` token. The schema reader
    refuses provenance keys; this layer never supplies them. Raises `KeyError`
    when *section_set* does not declare the type.
    """
    frontmatter: dict[str, object] = {"title": proposal.title, "description": proposal.description}
    if entry.promotion is not None and on is not None:
        frontmatter.update(entry.promotion.frontmatter_on(on))
    return PageRender(type=entry.name, body=render_skeleton(section_set.types[entry.name]), frontmatter=frontmatter)


def plan_promotion(
    bundle: Bundle,
    pool: ProposalPool,
    proposal: Proposal,
    *,
    section_set: SectionSet,
    by: str,
    at: datetime,
    on: date,
) -> PagePlan:
    """Plan promotion using the pool's resolved type and its promotion block.

    Preserve recorded targets for updates and undated types. Only a new dated
    promotion derives a target from its title and date. Work types refuse with
    `type-unavailable`: `gw work file` is the sole work-item creator, so ordinary
    promotion never applies work metadata or flips the proposal to `created`.
    """
    resolved = pool.type_for(proposal)
    if not isinstance(resolved, Refusal) and resolved.directory == "work/":
        resolved = Refusal(
            path=proposal.member,
            kind="type-unavailable",
            detail=f"{resolved.name}: work items must be filed through gw work file; ordinary promotion is unavailable",
        )
    if isinstance(resolved, Refusal):
        return PagePlan(
            root=bundle.root,
            target=proposal.target,
            mode=target_mode(bundle, proposal),
            proposal=proposal.member,
            writes=(),
            refusals=(resolved,),
        )
    target = proposal.target
    if target_mode(bundle, proposal) == "create" and resolved.promotion is not None and resolved.promotion.dated:
        target = pool.target_for(resolved.name, proposal.title, on=on)
    plan = plan_promote(
        bundle,
        replace(proposal, target=target),
        page_render(resolved, proposal, section_set=section_set, on=on),
        by=by,
        at=at,
    )
    return _retargeted(plan, proposal.member, target)


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

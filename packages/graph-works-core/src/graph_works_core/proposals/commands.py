"""Filing, deciding and listing curated-page proposals, plan-by-default (ADR-0022).

This vertical owns target normalization, the bundle/lane-schema load sequence,
and the plan/apply composition so interfaces only route, parse, format, and
read the clock. Content refusals remain values; invalid caller input such as a
naive timestamp or undeclared lane still raises.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from doc_wiki_okf.actors import human_actor
from doc_wiki_okf.proposals import lane_set, plan_file
from okf_ext.bundle import SCHEMA_DIRNAME
from okf_ext.proposals import (
    PAGE_STATUSES,
    ApplyResult,
    Decision,
    DecisionPlan,
    Mode,
    Proposal,
    ProposalPlan,
    Refusal,
    apply,
    list_proposals,
    plan_decide,
)
from okf_ext.proposals import mode as target_mode
from okf_ext.schemas import load_schemas
from okf_io import Bundle, load_bundle

from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.layout import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class ProposalRefusal:
    """One reason a proposal run will not apply."""

    path: str
    kind: str
    detail: str


def _lift(refusals: tuple[Refusal, ...]) -> tuple[ProposalRefusal, ...]:
    return tuple(ProposalRefusal(path=refusal.path, kind=refusal.kind, detail=refusal.detail) for refusal in refusals)


def _require_aware(at: datetime) -> None:
    """Raise when the caller supplied an instant without a timezone."""
    if at.tzinfo is None or at.tzinfo.utcoffset(at) is None:
        raise ValueError(
            f"`at` must be timezone-aware, got {at!r}. These packages never read the clock: the caller supplies "
            "the instant, and a naive one lands in `generated.at` as a value okf-io cannot coerce."
        )


@dataclass(frozen=True, slots=True)
class ProposalDecideRun:
    """What one decision planned and, when applied, wrote."""

    target: str
    decision: Decision
    proposal: str | None
    plan: DecisionPlan | None
    refusals: tuple[ProposalRefusal, ...]
    result: ApplyResult | None = None

    @property
    def ok(self) -> bool:
        return not self.refusals and (self.result is None or self.result.ok)


@dataclass(frozen=True, slots=True)
class ProposalFileRun:
    """What one proposal filing planned and, when applied, wrote."""

    lane: str
    target: str
    proposal: str
    plan: ProposalPlan
    refusals: tuple[ProposalRefusal, ...]
    result: ApplyResult | None = None

    @property
    def ok(self) -> bool:
        return not self.refusals and (self.result is None or self.result.ok)


def normalize_target(raw: str) -> str:
    """Turn caller text into a usable bundle-relative proposal identity, or ``""``."""
    cleaned = raw.strip().replace("\\", "/").lstrip("/")
    if not cleaned:
        return ""
    parts: list[str] = []
    for part in PurePosixPath(cleaned).parts:
        if part == ".":  # pragma: no cover -- PurePosixPath.parts never yields "."
            continue
        if part == "..":
            if not parts:
                return ""
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def find_proposal(bundle: Bundle, target: str) -> Proposal | None:
    """Find a proposal exclusively through its normalized target identity."""
    wanted = normalize_target(target)
    if not wanted:
        return None
    for proposal in list_proposals(bundle):
        if proposal.target == wanted:
            return proposal
    return None


def run_proposal_decide(
    layout: WorkspaceLayout,
    target: str,
    decision: Decision,
    *,
    by: str | None = None,
    at: datetime,
    dry_run: bool = True,
    before_apply: Callable[[ProposalDecideRun], None] | None = None,
) -> ProposalDecideRun:
    """Plan -- and unless ``dry_run``, apply -- one proposal decision.

    *by* is recorded in ``verified[]``. Omitted, it is ``human:<handle>`` read
    from git in the workspace root, so a decision made by hand is never stamped
    with a string okf-io's actor convention rejects.

    `before_apply`, when supplied on a live call, inspects the actual candidate
    with application fields empty before any domain write. Raising aborts the
    call; exceptions propagate. The callback must not mutate the candidate or
    workspace. Dry runs never invoke it. Omitting it preserves CLI behavior.
    """
    _require_aware(at)
    normalized = normalize_target(target)
    bundle = load_bundle(layout.bundle_dir)
    proposal = find_proposal(bundle, target)
    if proposal is None:
        named = normalized or target
        refusal = ProposalRefusal(path=named, kind="no-proposal", detail=f"no proposal targets {named!r}")
        run = ProposalDecideRun(target=normalized, decision=decision, proposal=None, plan=None, refusals=(refusal,))
        if not dry_run and before_apply is not None:
            before_apply(run)
        return run
    plan = plan_decide(bundle, proposal, decision, by=by or human_actor(layout.root), at=at)
    run = ProposalDecideRun(
        target=normalized,
        decision=decision,
        proposal=proposal.member,
        plan=plan,
        refusals=_lift(plan.refusals),
    )
    if not dry_run and before_apply is not None:
        before_apply(run)
    if dry_run or not plan.ok or plan.is_empty:
        return run
    return ProposalDecideRun(
        target=run.target,
        decision=decision,
        proposal=run.proposal,
        plan=plan,
        refusals=run.refusals,
        result=apply(bundle, plan),
    )


def run_proposal_file(
    layout: WorkspaceLayout,
    *,
    lane: str,
    title: str,
    description: str,
    source: Mapping[str, Any],
    by: str,
    at: datetime,
    dry_run: bool = True,
) -> ProposalFileRun:
    """Plan -- and unless ``dry_run``, apply -- filing one source's proposal."""
    _require_aware(at)
    bundle = load_bundle(layout.bundle_dir)
    config = load_workspace_config(layout)
    lanes = lane_set(load_schemas(config.declarations_dir / SCHEMA_DIRNAME))
    plan = plan_file(bundle, lanes, lane=lane, title=title, description=description, source=source, by=by, at=at)
    run = ProposalFileRun(
        lane=lane,
        target=plan.target,
        proposal=plan.proposal,
        plan=plan,
        refusals=_lift(plan.refusals),
    )
    if dry_run or not plan.ok or plan.is_empty:
        return run
    return ProposalFileRun(
        lane=lane,
        target=plan.target,
        proposal=plan.proposal,
        plan=plan,
        refusals=run.refusals,
        result=apply(bundle, plan),
    )


@dataclass(frozen=True, slots=True)
class ProposalListing:
    """One proposal and whether promoting it would create or update its target."""

    proposal: Proposal
    mode: Mode


def run_proposals_read(layout: WorkspaceLayout, page_status: str = "proposed") -> tuple[ProposalListing, ...]:
    """Every proposal with *page_status* (default: the open ones), sorted by member. Never writes.

    The one read `gw wiki proposals` and `/v1/wiki/proposals` share, so the
    two cannot drift. `mode` is derived from the bundle and never stored.
    The bundle is loaded without `ignore=`, as the verb always has.

    Raises `ValueError` when *page_status* is not one of `PAGE_STATUSES`.
    """
    if page_status not in PAGE_STATUSES:
        raise ValueError(f"page_status {page_status!r} not in {'|'.join(PAGE_STATUSES)}")
    bundle = load_bundle(layout.bundle_dir)
    return tuple(
        ProposalListing(proposal=proposal, mode=target_mode(bundle, proposal))
        for proposal in list_proposals(bundle, page_status=page_status)
    )


__all__ = [
    "ProposalDecideRun",
    "ProposalFileRun",
    "ProposalListing",
    "ProposalRefusal",
    "find_proposal",
    "normalize_target",
    "run_proposal_decide",
    "run_proposal_file",
    "run_proposals_read",
]

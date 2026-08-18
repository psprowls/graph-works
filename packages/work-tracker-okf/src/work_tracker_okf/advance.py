"""The single mutation point for the frontmatter the routing table owns.

`advance` **plans**; it does not mutate (C3-A). A separate `apply` writes the
plan. That is okf-io's writer vocabulary -- `before`, `after`, `changed`,
`diff()` -- and it makes *which keys change when a stage completes*
inspectable before anything is written.

Refusals are data with a closed vocabulary, not exceptions: a page that is not
ready is a fact about content, and nothing on this package's content path
raises. `work-io` raised `ValueError` for all seven cases.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from okf_io import Document

from work_tracker_okf.items import WorkItem
from work_tracker_okf.workflow import PLAN_OR_EXECUTE, RouteResult, Transition, route, state_for

RefusalReason = Literal[
    "unknown-slug",
    "blocked",
    "nothing-to-advance",
    "effort-required",
    "owner-required",
    "resolved-in-required",
    "children-open",
]


@dataclass(frozen=True, slots=True)
class FieldChange:
    key: str
    before: object | None
    after: object | None


@dataclass(frozen=True, slots=True)
class AdvancePlan:
    """What advancing *slug* would change, and what it declines to change.

    `stamp_source` and `sync_plan_table` are **unresolved requests** (C3-K):
    turning the first into a `Source` needs child 2's path and upsert
    functions, and the second needs `okf_ext.tables` plus a row naming the
    plan artifact. Child 3 imports neither; child 6 composes them.
    """

    slug: str
    route: RouteResult
    transition: Transition | None
    changes: tuple[FieldChange, ...]
    stamp_source: str | None
    sync_plan_table: bool
    refusal: RefusalReason | None
    detail: str

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def diff(self) -> str:
        """Render the plan. Writes nothing."""
        if self.refusal is not None:
            return f"{self.slug}: refused ({self.refusal}) -- {self.detail}"
        if not self.changes:
            return f"{self.slug}: no change"
        lines = [f"{self.slug}:"]
        lines.extend(f"  {change.key}: {change.before!r} -> {change.after!r}" for change in self.changes)
        return "\n".join(lines)


def advance(
    items: Sequence[WorkItem],
    slug: str,
    *,
    today: date,
    effort: str | None = None,
    owner: str | None = None,
    resolved_in: str | None = None,
    worktree: str | None = None,
    branch: str | None = None,
) -> AdvancePlan:
    """Plan the next transition for *slug*. Mutates nothing, reads no clock.

    Takes `(items, slug)` rather than a pre-routed transition because routing,
    picking `on_dispatch or on_complete`, and refusing an unmet requirement are
    one decision -- splitting them across a CLI is how `work-io` ended up with
    the gate messages living away from the table that produces them.
    """
    item = next((candidate for candidate in items if candidate.slug == slug), None)
    state = state_for(items, slug, effort=effort)
    if item is None or state is None:
        return _refused(slug, None, None, "unknown-slug", f"unknown slug {slug!r}")
    result = route(state)
    if result.blockers:
        return _refused(slug, result, None, "blocked", "; ".join(result.blockers))
    transition = result.on_dispatch or result.on_complete
    if transition is None:
        return _refused(slug, result, None, "nothing-to-advance", f"nothing to advance: {result.reason}")
    # Two independent guards on the sentinel, because a leak writes an invalid
    # enum value into a real page.
    if "effort" in transition.requires or transition.phase == PLAN_OR_EXECUTE:
        return _refused(
            slug,
            result,
            transition,
            "effort-required",
            "effort required to advance: pass effort=xtra-small|small|medium|large|xtra-large",
        )
    if "children-terminal" in transition.requires:
        open_slugs = ", ".join(state.child_rollup.open_slugs) if state.child_rollup else ""
        return _refused(
            slug,
            result,
            transition,
            "children-open",
            f"waiting on children: {open_slugs}; finish them or detach (delete the child's parent key)",
        )
    if "owner" in transition.requires and not (owner or item.owner):
        return _refused(slug, result, transition, "owner-required", "owner required to advance: pass owner=<handle>")
    if "resolved_in" in transition.requires and not (resolved_in or item.resolved_in):
        return _refused(
            slug,
            result,
            transition,
            "resolved-in-required",
            "resolved_in required to advance: pass resolved_in=<pr or commit>",
        )
    return AdvancePlan(
        slug=slug,
        route=result,
        transition=transition,
        changes=_changes(
            item,
            transition,
            today=today,
            effort=effort,
            owner=owner,
            resolved_in=resolved_in,
            worktree=worktree,
            branch=branch,
        ),
        stamp_source=transition.stamp_source,
        sync_plan_table=transition.sync_plan_table,
        refusal=None,
        detail=result.reason,
    )


def _refused(
    slug: str,
    result: RouteResult | None,
    transition: Transition | None,
    reason: RefusalReason,
    detail: str,
) -> AdvancePlan:
    """A refusal carries **empty changes** -- which is what makes `apply` safe
    with no guard of its own."""
    return AdvancePlan(
        slug=slug,
        route=result if result is not None else RouteResult(dispatch=None, reason=detail),
        transition=transition,
        changes=(),
        stamp_source=None,
        sync_plan_table=False,
        refusal=reason,
        detail=detail,
    )


def _changes(
    item: WorkItem,
    transition: Transition,
    *,
    today: date,
    effort: str | None,
    owner: str | None,
    resolved_in: str | None,
    worktree: str | None = None,
    branch: str | None = None,
) -> tuple[FieldChange, ...]:
    """The keys to write, in the order they are written -- which is also the
    order they append in on a page that lacks them."""
    candidates: tuple[tuple[str, object | None, object | None], ...] = (
        ("phase", item.phase, transition.phase),
        ("workflow_status", item.workflow_status, transition.workflow_status),
        ("status", item.status, transition.document_status),
        ("effort", item.effort, effort),
        ("owner", item.owner, owner),
        ("resolved_in", item.resolved_in, resolved_in),
        ("worktree", item.worktree, worktree),
        ("branch", item.branch, branch),
    )
    changes = [
        FieldChange(key, before, after) for key, before, after in candidates if after is not None and after != before
    ]
    # `updated` is written as a `date`, not an ISO string (C3-J): ruamel quotes
    # a `str` that would re-parse as a date, and every authored page is bare.
    if item.updated != today.isoformat():
        changes.append(FieldChange("updated", item.updated, today))
    return tuple(changes)


def apply(document: Document, plan: AdvancePlan) -> None:
    """Write *plan* into *document*. Nothing else on the page is touched.

    The assertion is the invariant made checkable: a refused plan has empty
    `changes`, so applying one would be a no-op anyway -- but a caller that
    reaches here has a bug, not a content problem.
    """
    assert plan.refusal is None, f"refused plan ({plan.refusal}) must not be applied"
    for change in plan.changes:
        document.set(change.key, change.after)


__all__ = ["AdvancePlan", "FieldChange", "RefusalReason", "advance", "apply"]

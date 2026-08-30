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

from work_tracker_okf.hierarchy import active_nonterminal_descendants
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import PARENT_TYPES
from work_tracker_okf.workflow import PLAN_OR_EXECUTE, RouteResult, Transition, route, state_for

RefusalReason = Literal[
    "unknown-path",
    "blocked",
    "nothing-to-advance",
    "effort-required",
    "owner-required",
    "resolved-in-required",
    "children-open",
    "released-at-required",
    "uncommitted-work",
    "no-commits",
    "return-not-available",
    "no-affects-touched",
]

#: The three reasons above that this module never produces itself. They are
#: raised one band up, by the `execute -> finish` commit gate in
#: `graph_works_core.orchestrate.stage_advance`, which cannot own the
#: vocabulary: `RefusalReason` is the CLI's rendering contract
#: (`rendering.advance_payload` reads `outcome.plan.refusal`), and a closed
#: string vocabulary is band-legal here where a git observation is not.
#: `return-not-available` sits between them in `RefusalReason` but is *not* a
#: member: `advance()` raises that one itself.
GATE_REFUSALS: frozenset[str] = frozenset({"uncommitted-work", "no-commits", "no-affects-touched"})


@dataclass(frozen=True, slots=True)
class FieldChange:
    key: str
    before: object | None
    after: object | None


@dataclass(frozen=True, slots=True)
class AdvancePlan:
    """What advancing a canonical item path would change or decline to change.

    `stamp_source` and `sync_plan_table` are **unresolved requests** (C3-K):
    turning the first into a `Source` needs child 2's path and upsert
    functions, and the second needs `okf_ext.tables` plus a row naming the
    plan artifact. Child 3 imports neither; child 6 composes them.
    """

    path: str
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
            return f"{self.path}: refused ({self.refusal}) -- {self.detail}"
        if not self.changes:
            return f"{self.path}: no change"
        lines = [f"{self.path}:"]
        lines.extend(f"  {change.key}: {change.before!r} -> {change.after!r}" for change in self.changes)
        return "\n".join(lines)


def advance(
    items: Sequence[WorkItem],
    path: str,
    *,
    today: date,
    effort: str | None = None,
    owner: str | None = None,
    resolved_in: str | None = None,
    released_at: date | None = None,
    worktree: str | None = None,
    branch: str | None = None,
    return_: bool = False,
) -> AdvancePlan:
    """Plan the next transition for *path*. Mutates nothing, reads no clock.

    `return_=True` selects the table's `on_return` instead of
    `on_dispatch or on_complete` -- the one backwards move, offered only at
    `finish`. It is mutually exclusive with `resolved_in`: a return is not a
    stage completion, and silently ignoring a `resolved_in` handed to one
    would record nothing while looking like it had.

    Takes `(items, path)` rather than a pre-routed transition because routing,
    picking `on_dispatch or on_complete`, and refusing an unmet requirement are
    one decision -- splitting them across a CLI is how `work-io` ended up with
    the gate messages living away from the table that produces them.
    """
    item = next((candidate for candidate in items if candidate.path == path), None)
    state = state_for(items, path, effort=effort)
    if item is None or state is None:
        return _refused(path, None, None, "unknown-path", f"unknown path {path!r}")
    result = route(state)
    if result.blockers:
        return _refused(path, result, None, "blocked", "; ".join(result.blockers))
    if return_:
        if resolved_in is not None:
            return _refused(
                path,
                result,
                None,
                "return-not-available",
                "--return is mutually exclusive with --resolved-in: a return is not a stage completion",
            )
        transition = result.on_return
        if transition is None:
            return _refused(
                path,
                result,
                None,
                "return-not-available",
                f"no return path from phase {item.phase!r}: --return applies to an item at phase 'finish'",
            )
    else:
        transition = result.on_dispatch or result.on_complete
        if transition is None:
            return _refused(path, result, None, "nothing-to-advance", f"nothing to advance: {result.reason}")
    # Two independent guards on the sentinel, because a leak writes an invalid
    # enum value into a real page.
    if "effort" in transition.requires or transition.phase == PLAN_OR_EXECUTE:
        return _refused(
            path,
            result,
            transition,
            "effort-required",
            "effort required to advance: pass effort=xtra-small|small|medium|large|xtra-large",
        )
    if state.type in PARENT_TYPES and transition.work_status == "resolved":
        open_descendants = active_nonterminal_descendants(items, item.path)
        if open_descendants:
            return _refused(
                path,
                result,
                transition,
                "children-open",
                "waiting on descendants: " + ", ".join(open_descendants),
            )
    if state.type == "Release" and transition.work_status == "resolved" and not (released_at or item.released_at):
        return _refused(
            path,
            result,
            transition,
            "released-at-required",
            "released_at required to resolve a Release",
        )
    if "owner" in transition.requires and not (owner or item.owner):
        return _refused(path, result, transition, "owner-required", "owner required to advance: pass owner=<handle>")
    if "resolved_in" in transition.requires and not (resolved_in or item.resolved_in):
        return _refused(
            path,
            result,
            transition,
            "resolved-in-required",
            "resolved_in required to advance: pass resolved_in=<pr or commit>",
        )
    return AdvancePlan(
        path=path,
        route=result,
        transition=transition,
        changes=_changes(
            item,
            transition,
            today=today,
            effort=effort,
            owner=owner,
            resolved_in=resolved_in,
            released_at=released_at,
            worktree=worktree,
            branch=branch,
        ),
        stamp_source=transition.stamp_source,
        sync_plan_table=transition.sync_plan_table,
        refusal=None,
        detail=result.reason,
    )


def _refused(
    path: str,
    result: RouteResult | None,
    transition: Transition | None,
    reason: RefusalReason,
    detail: str,
) -> AdvancePlan:
    """A refusal carries **empty changes** -- which is what makes `apply` safe
    with no guard of its own."""
    return AdvancePlan(
        path=path,
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
    released_at: date | None,
    worktree: str | None = None,
    branch: str | None = None,
) -> tuple[FieldChange, ...]:
    """The keys to write, in the order they are written -- which is also the
    order they append in on a page that lacks them."""
    candidates: tuple[tuple[str, object | None, object | None], ...] = (
        ("phase", item.phase, transition.phase),
        ("work_status", item.work_status, transition.work_status),
        ("status", item.status, transition.document_status),
        ("effort", item.effort, effort),
        ("owner", item.owner, owner),
        ("resolved_in", item.resolved_in, resolved_in),
        ("released_at", item.released_at, released_at),
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


__all__ = ["GATE_REFUSALS", "AdvancePlan", "FieldChange", "RefusalReason", "advance", "apply"]

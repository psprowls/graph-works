"""Archiving a terminal item or wiki page, and the pointer that has to go
with it.

`work_tracker_okf.archive` owns the work-item move -- `plan_archive` /
`apply_archive` -- and `doc_wiki_okf.archive` owns the wiki-page move, under
the same two names, aliased on import so one module can hold both. Neither
package is `WorkspaceLayout`-shaped, so neither can call upward into a
function like `provenance.clear_active_work` -- this module is the
composition, same as it always was for the pointer clear.

Clearing the pointer is *behaviour*, so it belongs here rather than in the CLI
that will route to this function: the graph-works-cli design rule is a thin
Typer CLI with all logic in core.

Both plans are computed against one bundle snapshot, but applying them is a
second hazard beyond either plan's own `ok`: `okf_ext.moves.apply` never
updates the in-memory `Bundle` (its own docstring: "the caller reloads"), so
applying the two plans sequentially against that same stale snapshot is unsafe
whenever one lane's referrer rewrite target is also the other lane's move
source -- the second apply would move that member using its pre-rewrite
content, silently discarding the first apply's already-written correction.
`_touched_members` and the pre-apply conflict check in `run_archive` are the
guard against that.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from doc_wiki_okf.archive import ARCHIVE_IGNORE as WIKI_ARCHIVE_IGNORE
from doc_wiki_okf.archive import ArchivePlan as WikiArchivePlan
from doc_wiki_okf.archive import ArchiveResult as WikiArchiveResult
from doc_wiki_okf.archive import apply_archive as apply_wiki_archive
from doc_wiki_okf.archive import plan_archive as plan_wiki_archive
from okf_ext.moves import MovePlan
from okf_io import load_bundle
from work_tracker_okf.archive import ArchivePlan, ArchiveResult, apply_archive, plan_archive
from work_tracker_okf.compose import append_lane_log
from work_tracker_okf.items import ARCHIVE_IGNORE

from graph_works_core.workspace import provenance
from graph_works_core.workspace.layout import WorkspaceLayout


def _touched_members(plan: MovePlan) -> frozenset[str]:
    """Every member a plan will move or rewrite the body/frontmatter of.

    Applying two independently-planned `MovePlan`s sequentially against the
    same in-memory bundle is unsafe when one plan's referrer rewrite target is
    also the other plan's move source: `okf_ext.moves.apply` never updates the
    in-memory `Bundle` (its own docstring: "the caller reloads"), so the
    second apply would move that member using its pre-rewrite content,
    silently discarding the first apply's already-written correction. This is
    the set the two lanes' plans are checked for overlap against before
    either is allowed to apply.
    """
    return frozenset({move.source for move in plan.moves} | {edit.member for edit in plan.edits})


@dataclass(frozen=True, slots=True)
class ArchiveRun:
    """What one archive did, work-item half and wiki half. `result`/`wiki` are
    `None` for a dry run, a refused plan, or a cross-lane conflict -- the
    three cases where nothing was applied, so there is nothing to report but
    the plans themselves."""

    plan: ArchivePlan
    wiki_plan: WikiArchivePlan
    conflict: tuple[str, ...] = ()
    result: ArchiveResult | None = None
    wiki: WikiArchiveResult | None = None
    pointer_cleared: bool = False
    logged: str | None = None

    @property
    def ok(self) -> bool:
        """False when either half's plan carries a refusal, or the two halves
        would corrupt each other's referrer rewrites if both applied against
        the same stale bundle snapshot. All-or-nothing across both lanes: a
        refusal -- or a conflict -- on one side blocks the other's apply too,
        extending the existing "a sweep fails closed" invariant."""
        return self.plan.ok and self.wiki_plan.ok and not self.conflict


def run_archive(
    layout: WorkspaceLayout,
    slugs: Sequence[str] | None = None,
    wiki_slugs: Sequence[str] | None = (),
    *,
    today: date,
    dry_run: bool = True,
) -> ArchiveRun:
    """Archive *slugs* (work items), or every eligible item when `None`; and
    *wiki_slugs* (path-qualified wiki page tokens, e.g. `"adrs/2026-08-12-foo"`),
    or every eligible proposal when `None`.

    `wiki_slugs` defaults to `()` -- zero wiki involvement -- so every caller
    that predates the wiki half is unaffected.

    The bundle is loaded through the **union** of both packages' wide lenses,
    never either package's narrow one: each side's `moves` call needs its own
    per-item or per-page artifacts visible to plan correctly.

    Both plans are computed before either applies. If either is not `ok`,
    **neither side applies** -- extending the existing "a sweep fails closed"
    invariant across both lanes rather than silently archiving one half while
    the other refuses.

    A third refusal sits alongside those two: if the two plans' touched
    members overlap -- one lane's referrer rewrite target is also the other
    lane's move source -- applying both against the same stale bundle
    snapshot would have the second apply move that member using its
    pre-rewrite content, silently discarding the first apply's correction.
    `_touched_members` computes that overlap and, when non-empty, neither
    side applies, same as a plan refusal.

    The pointer is cleared from `result.archived` -- what actually moved on
    the work-item side -- not from *slugs*, and never from the wiki side: a
    wiki page carries no active-work pointer.

    `dry_run=True` by default, matching every writer in this workspace.

    No clock. `today=` is required, the way every writer in this workspace
    takes it.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=(*ARCHIVE_IGNORE, *WIKI_ARCHIVE_IGNORE))
    plan = plan_archive(bundle, slugs)
    wiki_plan = plan_wiki_archive(bundle, wiki_slugs)
    conflict = tuple(sorted(_touched_members(plan.moves) & _touched_members(wiki_plan.moves)))
    if dry_run or not plan.ok or not wiki_plan.ok or conflict:
        return ArchiveRun(plan=plan, wiki_plan=wiki_plan, conflict=conflict)

    result = apply_archive(bundle, plan)
    wiki_result = apply_wiki_archive(bundle, wiki_plan)
    cleared = provenance.clear_active_work(layout, set(result.archived))

    messages: list[str] = []
    if result.archived:
        messages.append(f"archived {', '.join(result.archived)}")
    if wiki_result.archived:
        messages.append(f"archived wiki {', '.join(wiki_result.archived)}")
    logged = append_lane_log(layout.bundle_dir, "; ".join(messages), on=today) if messages else None

    return ArchiveRun(
        plan=plan,
        wiki_plan=wiki_plan,
        result=result,
        wiki=wiki_result,
        pointer_cleared=cleared,
        logged=logged,
    )


__all__ = ["ArchiveRun", "run_archive"]

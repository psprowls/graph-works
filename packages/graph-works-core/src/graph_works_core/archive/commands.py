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

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from doc_wiki_okf.archive import ARCHIVE_IGNORE as WIKI_ARCHIVE_IGNORE
from doc_wiki_okf.archive import ArchivePlan as WikiArchivePlan
from doc_wiki_okf.archive import ArchiveResult as WikiArchiveResult
from doc_wiki_okf.archive import apply_archive as apply_wiki_archive
from doc_wiki_okf.archive import plan_archive as plan_wiki_archive
from doc_wiki_okf.archive import wiki_lanes
from doc_wiki_okf.resources import seeded_schema_set
from okf_ext.bundle import SCHEMA_DIRNAME
from okf_ext.moves import MovePlan, stranded_warning
from okf_ext.schemas import load_schemas
from okf_io import append_log_entry, load, load_bundle
from okf_io import parse as parse_document
from work_tracker_okf.archive import plan_archive
from work_tracker_okf.items import ARCHIVE_IGNORE, load_items
from work_tracker_okf.mutation import PlannedWrite, WorkMutationPlan

from graph_works_core.workspace import provenance
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repos
from graph_works_core.workspace.transactions import MutationApplication, apply_mutation


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


def _wiki_lanes_for(layout: WorkspaceLayout) -> tuple[str, ...]:
    """The wiki lane vocabulary this workspace declares.

    The workspace's own `.gw/schema/` when it has one, so archiving follows the
    layout actually on disk rather than the installed package's assumption --
    a workspace that has not yet run `gw config sync` still archives correctly.
    Gated on the directory existing, the same way `lint_drift.lanes._wiki_rules`
    gates its `schema_rule`; without one, the package's own seeded schemas are
    the fallback, which is precisely the behavior the deleted `WIKI_LANES`
    constant had.
    """
    schema_dir = layout.config_dir / SCHEMA_DIRNAME
    schema_set = load_schemas(schema_dir) if schema_dir.is_dir() else seeded_schema_set()
    return wiki_lanes(schema_set)


@dataclass(frozen=True, slots=True)
class ArchiveRun:
    """What one archive did, work-item half and wiki half. `result`/`wiki` are
    `None` for a dry run, a refused plan, or a cross-lane conflict -- the
    three cases where nothing was applied, so there is nothing to report but
    the plans themselves."""

    plan: WorkMutationPlan
    wiki_plan: WikiArchivePlan
    conflict: tuple[str, ...] = ()
    result: MutationApplication | None = None
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


def stranded_warnings(run: ArchiveRun) -> tuple[str, ...]:
    """Project lane-labelled opaque wikilink diagnostics for an archive run."""
    work_entries = () if run.plan.move_plan is None else run.plan.move_plan.stranded
    warnings: list[str] = []
    for label, entries in (("work items", work_entries), ("wiki pages", run.wiki_plan.moves.stranded)):
        warning = stranded_warning(entries)
        if warning is not None:
            warnings.append(f"{label}: {warning}")
    return tuple(warnings)


def _plan_with_log_entry(plan: WorkMutationPlan, bundle_root: Path, today: date, logged: str) -> WorkMutationPlan:
    """Fold the archive's `log.md` entry into *plan*, chaining onto any
    existing `log.md` write (from reference repair) instead of layering a
    second, independently-sourced write onto the same member -- see
    `work/bug-archive-duplicate-log-write` for why that trips the transaction
    layer's duplicate-target guard."""
    existing = next((write for write in plan.writes if write.member == "log.md"), None)
    base = (
        parse_document(existing.after.decode("utf-8"), path=bundle_root / "log.md")
        if existing is not None
        else load(bundle_root / "log.md")
    )
    log = append_log_entry(base, logged, on=today, dry_run=True)
    before_digest = (
        existing.before_digest if existing is not None else hashlib.sha256(log.before.encode("utf-8")).hexdigest()
    )
    source_member = existing.source_member if existing is not None else None
    merged = PlannedWrite("log.md", before_digest, log.after.encode("utf-8"), source_member)
    others = tuple(write for write in plan.writes if write.member != "log.md")
    return replace(plan, writes=tuple(sorted((*others, merged), key=lambda write: write.member)))


def run_archive(
    layout: WorkspaceLayout,
    paths: Sequence[str] | str | None = None,
    wiki_slugs: Sequence[str] | None = (),
    *,
    today: date,
    dry_run: bool = True,
    before_apply: Callable[[ArchiveRun], None] | None = None,
) -> ArchiveRun:
    """Archive canonical work-item *paths*, or every eligible item when `None`.
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
    The pointer is cleared only when at least one work root archived.

    When the wiki plan rewrites a link in `log.md`, the archive entry is
    appended after the wiki apply rather than folded into the work transaction,
    because the rewrite is planned against the pre-archive snapshot.

    `dry_run=True` by default, matching every writer in this workspace.

    `before_apply`, when supplied on a live call, inspects the actual candidate
    with application fields empty before any domain write. Raising aborts the
    call; exceptions propagate. The callback must not mutate the candidate or
    workspace. Dry runs never invoke it. Omitting it preserves CLI behavior.

    No clock. `today=` is required, the way every writer in this workspace
    takes it.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=(*ARCHIVE_IGNORE, *WIKI_ARCHIVE_IGNORE))
    plan = plan_archive(bundle, load_items(bundle), paths)
    wiki_plan = plan_wiki_archive(bundle, wiki_slugs, lanes=_wiki_lanes_for(layout))
    work_touched = {
        *(move.source for move in plan.moves),
        *(move.dest for move in plan.moves),
        *(write.member for write in plan.writes),
        *plan.deletes,
    }
    conflict = tuple(sorted(work_touched & _touched_members(wiki_plan.moves)))

    work_roots = tuple(
        source
        for source in plan.path_mapping
        if not any(source.startswith(f"{other}/children/") for other in plan.path_mapping if other != source)
    )
    messages = [f"archived {', '.join(work_roots)}"] if work_roots else []
    if wiki_plan.tokens:
        messages.append(f"archived wiki {', '.join(wiki_plan.tokens)}")
    logged = "; ".join(messages) or None
    # The wiki half's referrer rewrite of log.md is planned against the
    # pre-archive snapshot; folding the entry into the work transaction first
    # would have that rewrite silently erase it. Defer the append past the
    # wiki apply instead, against the freshly written file.
    defer_log = logged is not None and any(edit.member == "log.md" for edit in wiki_plan.moves.edits)
    if logged is not None and not defer_log:
        plan = _plan_with_log_entry(plan, bundle.root, today, logged)
    candidate = ArchiveRun(plan=plan, wiki_plan=wiki_plan, conflict=conflict, logged=logged)
    if not dry_run and before_apply is not None:
        before_apply(candidate)
    if dry_run or not plan.ok or not wiki_plan.ok or conflict:
        return candidate

    # `bundle` (line 166) was loaded with a wider ignore set than IGNORE
    # (ARCHIVE_IGNORE + WIKI_ARCHIVE_IGNORE) -- not eligible as baseline_bundle.
    result = apply_mutation(layout, plan, repo_roots=resolve_repos(layout))
    if not result.ok:
        return ArchiveRun(plan=plan, wiki_plan=wiki_plan, result=result, logged=logged)

    # The work journal is complete before wiki mutation begins. A wiki preflight
    # refusal above therefore blocks both sides; a failed work transaction never
    # reaches this call.
    wiki_result = apply_wiki_archive(bundle, wiki_plan)
    if defer_log:
        assert logged is not None  # narrowed by defer_log
        append_log_entry(load(bundle.root / "log.md"), logged, on=today, dry_run=False)
    # An empty set still deletes an *invalid* legacy pointer inside
    # clear_active_work; a wiki-only archive must not have that side effect.
    cleared = provenance.clear_active_work(layout, set(work_roots)) if work_roots else False

    return ArchiveRun(
        plan=plan,
        wiki_plan=wiki_plan,
        result=result,
        wiki=wiki_result,
        pointer_cleared=cleared,
        logged=logged,
    )


__all__ = ["ArchiveRun", "run_archive", "stranded_warnings"]

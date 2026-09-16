"""`gw work record-placement`: write an observed worktree/branch, nothing else.

The third orchestrate module, beside the planner (`commands.py`) and the
stage-completion shell (`stage_advance.py`). It shares no module-level symbol
with either; what it shares with `stage_advance` is the decision owner's lock.

The coordinator reads where Orca actually placed a worker and records that
pair here, immediately after launch (D-006). A live record re-reads the bundle
and re-plans inside `locked_decision_owner`, so it serializes with
`gw work advance`: when a worker's own advance wins the lock first, the record
sees the new phase and refuses rather than stamping a stage that already ended.
The lock prevents lost updates; it does not remove that race, and the refusal
is how the race is made visible.

Nothing here runs git or reads Orca. The pair is the caller's verified
observation; this module proves only that the item is entitled to it now.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from okf_io import Bundle, load_bundle, parse
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.mutation import PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import item_page
from work_tracker_okf.placement import PlacementPlan, apply_placement, plan_placement

from graph_works_core.workspace.decision_owner import locked_decision_owner
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repo
from graph_works_core.workspace.transactions import MutationApplication, apply_mutation


@dataclass(frozen=True, slots=True)
class PlacementRecord:
    """What one record did. `application` is `None` for a dry run, a refusal
    and an identical pair -- the three outcomes that write nothing."""

    plan: PlacementPlan
    application: MutationApplication | None = None
    repo_note: str | None = None

    @property
    def written(self) -> bool:
        return self.application is not None and self.application.ok


def run_record_placement(
    layout: WorkspaceLayout,
    path: str,
    *,
    root: str,
    phase: str,
    worktree: str,
    branch: str,
    today: date,
    dry_run: bool = True,
) -> PlacementRecord:
    """Record (*worktree*, *branch*) on *path* for its *phase* dispatch under *root*.

    Dry runs and unknown paths plan without locking, like `run_stage_advance`.
    A live record takes the decision owner's lock, re-plans against the
    projection read inside it, and applies one journaled page write before
    releasing it. A stale preimage returns a failed `MutationApplication`
    and writes nothing.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    if dry_run or not any(item.path == path for item in items):
        return PlacementRecord(
            plan=plan_placement(items, path, root=root, phase=phase, worktree=worktree, branch=branch, today=today)
        )
    with locked_decision_owner(layout, path) as context:
        plan = plan_placement(
            context.items, path, root=root, phase=phase, worktree=worktree, branch=branch, today=today
        )
        if plan.refusal is not None or not plan.changed:
            return PlacementRecord(plan=plan)
        repo, repo_note = resolve_repo(layout)
        application = apply_mutation(
            layout, _mutation(context.bundle, plan), repo_root=repo, baseline_bundle=context.bundle
        )
        return PlacementRecord(plan=plan, application=application, repo_note=repo_note)


def _mutation(bundle: Bundle, plan: PlacementPlan) -> WorkMutationPlan:
    member = item_page(plan.path).rel
    page = bundle.root / member
    before = bundle.concepts[plan.path].serialize().encode("utf-8")
    # Copy the authoritative projection, not a later disk read: an external
    # edit must fail the preimage check, never authorize this older plan.
    # Parsing a fresh document keeps the lock-held validation baseline intact.
    document = parse(before.decode("utf-8"), path=page)
    apply_placement(document, plan)
    return WorkMutationPlan(
        root=bundle.root,
        operation="file",
        path_mapping=MappingProxyType({}),
        move_plan=None,
        moves=(),
        writes=(PlannedWrite(member, hashlib.sha256(before).hexdigest(), document.serialize().encode("utf-8")),),
        deletes=(),
        mkdirs=(),
        warnings=(),
        refusals=(),
        validate_paths=(plan.path,),
        directory_preconditions=(),
    )


__all__ = ["PlacementRecord", "run_record_placement"]

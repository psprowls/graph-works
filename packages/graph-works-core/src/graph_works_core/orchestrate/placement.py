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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from okf_io import Bundle, load_bundle, parse
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.mutation import PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import item_page
from work_tracker_okf.placement import PlacementPlan, apply_placement, plan_placement

from graph_works_core.workspace.decision_owner import locked_decision_owner
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import ItemRepo, declared_repositories, resolve_item_repo, resolve_repos
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


def _prepare_placement(
    layout: WorkspaceLayout,
    items: Sequence[WorkItem],
    path: str,
    *,
    root: str,
    phase: str,
    worktree: str,
    branch: str,
    today: date,
    repo_name: str | None,
    repo: str | None,
) -> tuple[PlacementPlan, ItemRepo | None]:
    """Plan first, then resolve the item's repository for eligible placements."""
    by_path = {item.path: item for item in items}
    own: ItemRepo | None = None
    target: str | None = None
    if repo and path in by_path:
        declared = declared_repositories(layout)
        if repo not in declared:
            raise WorkspaceError(
                f"{path}: --repo {repo!r} names no declared repository in {layout.manifest_path}; "
                f"declared: {sorted(declared)}"
            )
        own = resolve_item_repo(layout, by_path[path], by_path, repo_name=repo_name)
        target = None if own.name == repo else repo
    plan = plan_placement(
        items,
        path,
        root=root,
        phase=phase,
        worktree=worktree,
        branch=branch,
        today=today,
        repo=target,
    )
    if plan.refusal is None and own is None:
        own = resolve_item_repo(layout, by_path.get(path), by_path, repo_name=repo_name)
    return plan, own


def run_record_placement(
    layout: WorkspaceLayout,
    path: str,
    *,
    root: str,
    phase: str,
    worktree: str,
    branch: str,
    today: date,
    repo_name: str | None = None,
    repo: str | None = None,
    dry_run: bool = True,
) -> PlacementRecord:
    """Record (*worktree*, *branch*) on *path* for its *phase* dispatch under *root*.

    Every eligible placement, including a preview or unchanged replay,
    validates *path*'s repository and returns its selection note. The code
    repo for postcondition validation is *path*'s own --
    `resolve_item_repo`'s strict chain: the nearest `repo:` over *path* and
    its ancestors, then *repo_name*, then the sole declared repository;
    several declared with no `repo:` and no *repo_name* still raise
    `WorkspaceError` rather than guess. The validation itself checks
    `affects` against every declared repo (`resolve_repos(layout)`), not
    only the resolved one -- matching `work file`/`work advance`
    (`stage_advance._advance`'s `repo_root=resolved_repo, repo_roots=declared`).
    The differential postcondition gate excuses a pre-existing
    `affects-missing` finding either way, so this only bites when baseline
    capture itself fails and the gate falls back to its absolute form.

    *repo* names the repository the observed pair actually lives in. When it
    names a declared repository other than *path*'s own, the pair is written
    under `repo_stamps[repo]` instead of the scalar `worktree`/`branch`
    pair; when it names *path*'s own, the scalar pair is written as usual.
    An undeclared *repo* raises `WorkspaceError`. *path*'s own repository is
    resolved (honouring *repo_name*) whenever *repo* is given, in both dry
    and live runs, so a dry run can plan the foreign-vs-own distinction too.

    Dry runs and unknown paths plan without locking, like `run_stage_advance`.
    A live record takes the decision owner's lock, re-plans and re-resolves
    against the projection read inside it, and applies one journaled page
    write before releasing it. A stale preimage returns a failed
    `MutationApplication` and writes nothing.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    known = any(item.path == path for item in items)
    if dry_run or not known:
        plan, own = _prepare_placement(
            layout,
            items,
            path,
            root=root,
            phase=phase,
            worktree=worktree,
            branch=branch,
            today=today,
            repo_name=repo_name,
            repo=repo,
        )
        return PlacementRecord(plan=plan, repo_note=own.note if own else None)
    with locked_decision_owner(layout, path) as context:
        plan, own = _prepare_placement(
            layout,
            context.items,
            path,
            root=root,
            phase=phase,
            worktree=worktree,
            branch=branch,
            today=today,
            repo_name=repo_name,
            repo=repo,
        )
        if plan.refusal is not None or not plan.changed:
            return PlacementRecord(plan=plan, repo_note=own.note if own else None)
        assert own is not None
        application = apply_mutation(
            layout,
            _mutation(context.bundle, plan),
            repo_root=own.path,
            repo_roots=resolve_repos(layout),
            baseline_bundle=context.bundle,
        )
        return PlacementRecord(plan=plan, application=application, repo_note=own.note)


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

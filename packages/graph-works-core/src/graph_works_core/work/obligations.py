"""Record a finish obligation through a guarded, committed page mutation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from okf_io import Bundle, parse
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.mutation import PlannedWrite, WorkMutationPlan
from work_tracker_okf.obligations import ObligationPlan, apply_obligations, plan_add
from work_tracker_okf.paths import item_page

from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.commits import WorkspaceCommit, commit_mode, item_stem
from graph_works_core.workspace.decision_owner import locked_decision_owner
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repos
from graph_works_core.workspace.transactions import MutationApplication, apply_mutation


@dataclass(frozen=True, slots=True)
class ObligationRecord:
    plan: ObligationPlan
    application: MutationApplication | None = None

    @property
    def written(self) -> bool:
        return self.application is not None and self.application.ok


def run_obligation_add(
    layout: WorkspaceLayout, path: str, *, text: str, on: date, dry_run: bool = True
) -> ObligationRecord:
    """Plan by default; re-plan under the decision owner lock before a write."""
    items = load_items(load_workspace_bundle(layout, ignore=IGNORE))
    if dry_run or not any(item.path == path for item in items):
        return ObligationRecord(plan_add(items, path, text, on=on))
    commit_mode(layout)
    with locked_decision_owner(layout, path) as context:
        plan = plan_add(context.items, path, text, on=on)
        if plan.refusal is not None or not plan.changed:
            return ObligationRecord(plan)
        application = apply_mutation(
            layout,
            _mutation(context.bundle, plan),
            repo_roots=resolve_repos(layout),
            baseline_bundle=context.bundle,
            commit=WorkspaceCommit(f"workspace: record {item_stem(path)} finish obligation", items=(path,)),
        )
        return ObligationRecord(plan, application)


def _mutation(bundle: Bundle, plan: ObligationPlan) -> WorkMutationPlan:
    member = item_page(plan.path).rel
    before = bundle.concepts[plan.path].serialize().encode("utf-8")
    document = parse(before.decode("utf-8"), path=bundle.root / member)
    apply_obligations(document, plan)
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


__all__ = ["ObligationRecord", "run_obligation_add"]

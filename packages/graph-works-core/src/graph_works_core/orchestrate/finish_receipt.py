"""Record observed Git integration evidence in one journaled workspace mutation."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import date
from types import MappingProxyType

from okf_io import load_bundle, parse
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref, item_page
from work_tracker_okf.sources import upsert

from graph_works_core.workspace.decision_owner import locked_decision_owner
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.finish import (
    finish_read_guard,
    historical_integration,
    observe_integration,
    read_finish_receipt,
    resolve_finish_targets,
)
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repos
from graph_works_core.workspace.transactions import apply_mutation


@dataclass(frozen=True, slots=True)
class FinishReceiptResult:
    refusal: str | None
    changed: bool
    receipt_path: str | None


def run_record_finish(layout: WorkspaceLayout, path: str, *, repo_name: str, today: date) -> FinishReceiptResult:
    """Callers name a target, never supply completion claims or commit identities."""
    if not any(i.path == path for i in load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))):
        return FinishReceiptResult("unknown finish owner", False, None)
    with locked_decision_owner(layout, path) as context:
        guard = finish_read_guard(layout, path, bundle=context.bundle)
        item = next((i for i in context.items if i.path == path), None)
        if item is None or item.phase != "finish" or item.work_status in {"resolved", "wontfix", "superseded"}:
            return FinishReceiptResult("owner must be active at finish", False, None)
        plan = resolve_finish_targets(layout, context.items, path)
        if plan.blockers:
            return FinishReceiptResult("; ".join(plan.blockers), False, None)
        target = next((t for t in plan.targets if t.repo.name == repo_name), None)
        if target is None:
            return FinishReceiptResult(f"{repo_name}: not an owned finish target", False, None)
        receipt, entries, error = read_finish_receipt(layout, path)
        if error:
            return FinishReceiptResult(error, False, None)
        if any(e.repo not in {t.repo.name for t in plan.targets} for e in entries):
            return FinishReceiptResult("finish receipt contains an unexpected repository", False, None)
        for entry in entries:
            entry_target = next(t for t in plan.targets if t.repo.name == entry.repo)
            if not historical_integration(entry_target, entry):
                return FinishReceiptResult(f"{entry.repo}: unverifiable historical receipt evidence", False, None)
        previous = next((e for e in entries if e.repo == repo_name), None)
        evidence = observe_integration(target, previous) if previous is not None else None
        evidence = evidence or observe_integration(target)
        if evidence is None:
            return FinishReceiptResult(
                f"{repo_name}: source is not verifiably integrated into {target.target_branch}", False, None
            )
        ref = artifact_ref(path, MANAGED_ARTIFACTS["finish-receipt"])
        receipt_before = receipt.serialize().encode("utf-8") if receipt is not None else None
        if receipt is None:
            receipt = parse(
                "---\ntype: Explanation\ntitle: Finish receipt\n"
                "description: Observed repository integration evidence.\nstatus: draft\n"
                f"created: {today.isoformat()}\nreceipt_version: 1\nowner: {path}\n"
                "integrations: []\n---\n\nVerified integration evidence.\n"
            )
        # Preserve stale entries as history and retain extension fields on every entry.
        raw_entries = receipt.fm_data()["integrations"]
        updated = False
        for raw_entry in raw_entries:
            if raw_entry["repo"] == repo_name:
                raw_entry.update(asdict(evidence))
                updated = True
        if not updated:
            raw_entries.append(asdict(evidence))
        receipt.set("integrations", raw_entries)
        page_before = context.bundle.concepts[path].serialize().encode("utf-8")
        page = parse(page_before.decode("utf-8"))
        upsert(page, ref, title="Finish receipt")
        if "[^finish-receipt]" not in page.body:
            newline = "\r\n" if "\r\n" in page_before.decode("utf-8") else "\n"
            page.set_body(page.body + f"{newline}[^finish-receipt]: [Finish receipt]({ref.resource}){newline}")
        writes = tuple(
            PlannedWrite(member, hashlib.sha256(before).hexdigest() if before is not None else None, after)
            for member, before, after in (
                (item_page(path).rel, page_before, page.serialize().encode("utf-8")),
                (ref.rel, receipt_before, receipt.serialize().encode("utf-8")),
            )
            if before != after
        )
        if not writes:
            return FinishReceiptResult(None, False, ref.rel)
        parent = ref.path(context.bundle.root).parent.relative_to(context.bundle.root).as_posix()
        mutation = WorkMutationPlan(
            root=context.bundle.root,
            operation="file",
            path_mapping=MappingProxyType({}),
            move_plan=None,
            moves=(),
            writes=writes,
            deletes=(),
            mkdirs=(parent,),
            warnings=(),
            refusals=(),
            validate_paths=(path,),
            directory_preconditions=()
            if (context.bundle.root / parent).exists()
            else (DirectoryPrecondition(parent, None),),
        )

        def validate() -> None:
            fresh_items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
            fresh_plan = resolve_finish_targets(layout, fresh_items, path)
            if (
                finish_read_guard(layout, path) != guard
                or fresh_plan != plan
                or observe_integration(target, evidence) is None
            ):
                raise WorkspaceError("finish owner, receipt, configuration or Git evidence changed; inspect and retry")

        application = apply_mutation(
            layout,
            mutation,
            repo_roots=resolve_repos(layout),
            baseline_bundle=context.bundle,
            validate_read_set=validate,
        )
        if not application.ok:
            return FinishReceiptResult(f"finish receipt transaction refused: {application}", False, ref.rel)
        return FinishReceiptResult(None, True, ref.rel)

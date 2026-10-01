"""Record observed Git integration evidence in one journaled workspace mutation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from okf_io import Document, parse
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import MANAGED_ARTIFACTS, ArtifactRef, artifact_ref, item_page
from work_tracker_okf.sources import upsert

from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.commits import CommitOutcome, WorkspaceCommit, commit_mode, item_stem
from graph_works_core.workspace.decision_owner import DecisionContext, locked_decision_owner
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.finish import (
    FinishPlan,
    VerifiedIntegration,
    discover_integration,
    finish_read_guard,
    finish_toolchain,
    historical_integration,
    read_finish_receipt,
    receipt_entry_data,
    resolve_finish_targets,
    verify_integration,
)
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repos
from graph_works_core.workspace.transactions import apply_mutation


@dataclass(frozen=True, slots=True)
class FinishReceiptResult:
    refusal: str | None
    changed: bool
    receipt_path: str | None
    commit: CommitOutcome | None = None
    warnings: tuple[str, ...] = ()


_RECEIPT_TEMPLATE = (
    "---\ntype: Explanation\ntitle: Finish receipt\n"
    "description: Observed repository integration evidence.\nstatus: draft\n"
    "created: {today}\nreceipt_version: 2\nowner: {path}\n"
    "integrations: []\n---\n\nVerified integration evidence.\n"
)
_ENTRY_KEYS = ("strategy", "evidence", "target_before", "accepted_by", "reason")


@dataclass(frozen=True, slots=True)
class ReceiptDraft:
    ref: ArtifactRef
    before: bytes | None
    after: bytes


def draft_receipt(layout: WorkspaceLayout, path: str, entry: VerifiedIntegration, *, today: date) -> ReceiptDraft:
    """The v2 receipt with *entry* upserted; other entries' bytes and extension fields kept.

    Callers have already refused a malformed receipt, so the read here only
    distinguishes absent from present.
    """
    ref = artifact_ref(path, MANAGED_ARTIFACTS["finish-receipt"])
    receipt, _entries, _error = read_finish_receipt(layout, path)
    before = receipt.serialize().encode("utf-8") if receipt is not None else None
    if receipt is None:
        receipt = parse(_RECEIPT_TEMPLATE.format(today=today.isoformat(), path=path))
    receipt.set("receipt_version", 2)
    raw_entries = receipt.fm_data()["integrations"]
    updated = False
    for raw_entry in raw_entries:
        if raw_entry["repo"] == entry.repo:
            # A new strategy must not inherit the old entry's optional fields.
            for key in _ENTRY_KEYS:
                raw_entry.pop(key, None)
            raw_entry.update(receipt_entry_data(entry))
            updated = True
    if not updated:
        raw_entries.append(receipt_entry_data(entry))
    receipt.set("integrations", raw_entries)
    return ReceiptDraft(ref, before, receipt.serialize().encode("utf-8"))


def link_receipt(page: Document, ref: ArtifactRef, *, newline: str) -> None:
    """Register the receipt in the owner's `sources[]` and footnote it once."""
    upsert(page, ref, title="Finish receipt")
    if "[^finish-receipt]" not in page.body:
        page.set_body(page.body + f"{newline}[^finish-receipt]: [Finish receipt]({ref.resource}){newline}")


def receipt_problem(layout: WorkspaceLayout, path: str, plan: FinishPlan) -> str | None:
    """Why the existing receipt blocks any new entry, else None. Read-only: safe before Git is touched."""
    _receipt, entries, error = read_finish_receipt(layout, path)
    if error:
        return error
    if any(e.repo not in {t.repo.name for t in plan.targets} for e in entries):
        return "finish receipt contains an unexpected repository"
    for entry in entries:
        entry_target = next(t for t in plan.targets if t.repo.name == entry.repo)
        if not historical_integration(entry_target, entry):
            return f"{entry.repo}: unverifiable historical receipt evidence"
    return None


@finish_toolchain
def run_record_finish(layout: WorkspaceLayout, path: str, *, repo_name: str, today: date) -> FinishReceiptResult:
    """Callers name a target, never supply completion claims or commit identities."""
    if not any(i.path == path for i in load_items(load_workspace_bundle(layout, ignore=IGNORE))):
        return FinishReceiptResult("unknown finish owner", False, None)
    commit_mode(layout)
    with locked_decision_owner(layout, path) as context:
        return record_finish_in(layout, context, path, repo_name=repo_name, today=today)


@finish_toolchain
def record_finish_in(
    layout: WorkspaceLayout,
    context: DecisionContext,
    path: str,
    *,
    repo_name: str,
    today: date,
    evidence: VerifiedIntegration | None = None,
) -> FinishReceiptResult:
    """Record using fresh inputs under the caller's existing decision-owner lock.

    *evidence*, when given (by `gw work integrate`), must verify live and is
    recorded as-is; otherwise the previous entry is kept when it still
    verifies, else the integration is rediscovered.

    Callers that change bundle content must refresh the context and verify the
    owner remains the locked owner before calling; ownership is never reacquired.
    """
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
    problem = receipt_problem(layout, path, plan)
    if problem is not None:
        return FinishReceiptResult(problem, False, None)
    entries = read_finish_receipt(layout, path)[1]
    previous = next((e for e in entries if e.repo == repo_name), None)
    if evidence is not None:
        reason = verify_integration(target, evidence)
        if reason is not None:
            return FinishReceiptResult(
                f"{repo_name}: supplied integration evidence does not verify: {reason}", False, None
            )
    elif previous is not None and verify_integration(target, previous) is None:
        evidence = previous
    else:
        found = discover_integration(target)
        if isinstance(found, str):
            return FinishReceiptResult(
                f"{repo_name}: source is not verifiably integrated into {target.target_branch}: {found}", False, None
            )
        evidence = found
    draft = draft_receipt(layout, path, evidence, today=today)
    ref = draft.ref
    page_before = context.bundle.concepts[path].serialize().encode("utf-8")
    page = parse(page_before.decode("utf-8"))
    link_receipt(page, ref, newline="\r\n" if b"\r\n" in page_before else "\n")
    writes = tuple(
        PlannedWrite(member, hashlib.sha256(before).hexdigest() if before is not None else None, after)
        for member, before, after in (
            (item_page(path).rel, page_before, page.serialize().encode("utf-8")),
            (ref.rel, draft.before, draft.after),
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
        fresh_items = load_items(load_workspace_bundle(layout, ignore=IGNORE))
        fresh_plan = resolve_finish_targets(layout, fresh_items, path)
        if (
            finish_read_guard(layout, path) != guard
            or fresh_plan != plan
            or verify_integration(target, evidence) is not None
        ):
            raise WorkspaceError("finish owner, receipt, configuration or Git evidence changed; inspect and retry")

    application = apply_mutation(
        layout,
        mutation,
        repo_roots=resolve_repos(layout),
        baseline_bundle=context.bundle,
        validate_read_set=validate,
        commit=WorkspaceCommit(f"workspace: record {item_stem(path)} finish receipt for {repo_name}", items=(path,)),
    )
    if not application.ok:
        return FinishReceiptResult(f"finish receipt transaction refused: {application}", False, ref.rel)
    return FinishReceiptResult(None, True, ref.rel, commit=application.commit, warnings=application.warnings)

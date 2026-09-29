"""Record human-attested integration evidence gw cannot verify mechanically.

Replaces the discouraged hand `gw work advance` after a rebase, an external
squash or a PR merged upstream. The entry is `attested`/`accepted`, never
`verified`; the answered ledger entry names who attested and why. Writes the
receipt and the ledger in one guarded mutation and never advances the item.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from okf_io import Document, load_bundle, parse
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import item_page
from work_tracker_okf.sources import upsert

from graph_works_core.orchestrate.finish_receipt import ReceiptDraft, draft_receipt, link_receipt
from graph_works_core.workspace.commits import WorkspaceCommit, commit_mode, item_stem
from graph_works_core.workspace.decision_owner import DecisionContext, locked_decision_owner
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.finish import (
    FinishTarget,
    VerifiedIntegration,
    finish_git,
    finish_read_guard,
    resolve_finish_targets,
)
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repos
from graph_works_core.workspace.transactions import apply_mutation

AcceptRefusal = Literal[
    "unknown-path",
    "not-at-finish",
    "no-target",
    "unknown-commit",
    "not-on-target",
    "missing-attribution",
    "receipt-refused",
]
_TERMINAL = frozenset({"resolved", "wontfix", "superseded"})


@dataclass(frozen=True, slots=True)
class AcceptIntegrationResult:
    path: str
    repo: str
    source_branch: str | None
    source_commit: str | None
    target_branch: str | None
    evidence: str | None
    accepted_by: str
    reason: str
    decision_id: str | None
    receipt_path: str | None
    ledger_path: str | None
    refusal: AcceptRefusal | None
    detail: str
    applied: bool


class _Refused(Exception):
    def __init__(self, refusal: AcceptRefusal, detail: str) -> None:
        super().__init__(detail)
        self.refusal = refusal
        self.detail = detail


def _resolve(repo: Path, ref: str) -> str | None:
    resolved = finish_git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    return resolved.stdout.strip() if resolved.returncode == 0 else None


def _observe(
    layout: WorkspaceLayout, context_items: Sequence[WorkItem], path: str, repo_name: str, evidence: str
) -> tuple[FinishTarget, str, str]:
    """(target, current source tip, full evidence SHA) or a refusal."""
    item = next((i for i in context_items if i.path == path), None)
    if item is None:
        raise _Refused("unknown-path", f"unknown work item {path!r}")
    if item.phase != "finish" or item.work_status in _TERMINAL:
        raise _Refused("not-at-finish", f"{path} is at {item.phase!r} ({item.work_status})")
    plan = resolve_finish_targets(layout, context_items, path)
    if plan.blockers:
        raise _Refused("no-target", "; ".join(plan.blockers))
    target = next((t for t in plan.targets if t.repo.name == repo_name), None)
    if target is None or target.repo.path is None:
        raise _Refused("no-target", f"{repo_name}: not an owned finish target of {path}")
    repo = target.repo.path
    source = _resolve(repo, "refs/heads/" + target.source_branch)
    tip = _resolve(repo, "refs/heads/" + target.target_branch)
    if source is None or tip is None:
        raise _Refused("no-target", f"{repo_name}: cannot resolve {target.source_branch!r} or {target.target_branch!r}")
    sha = _resolve(repo, evidence)
    if sha is None:
        raise _Refused("unknown-commit", f"{evidence!r} is not a commit in {repo_name}")
    if finish_git(repo, "merge-base", "--is-ancestor", sha, tip).returncode != 0:
        raise _Refused("not-on-target", f"{sha} is not reachable from {target.target_branch} in {repo_name}")
    return target, source, sha


def _mutation(
    context: DecisionContext, path: str, plan: _decisions.DecisionPlan, ledger_before: bytes | None, draft: ReceiptDraft
) -> WorkMutationPlan:
    root = context.bundle.root
    owner_path = context.owner.owner_path
    ledger_member = context.owner.ledger.relative_to(root).as_posix()
    ledger_after = _decisions.render(_decisions.parse(plan.snapshot.text).preamble, plan.after).encode("utf-8")
    pages: dict[str, tuple[bytes, Document]] = {}
    for member_path in dict.fromkeys((path, owner_path)):
        before = context.bundle.concepts[member_path].serialize().encode("utf-8")
        pages[member_path] = (before, parse(before.decode("utf-8")))
    item_before, item_doc = pages[path]
    link_receipt(item_doc, draft.ref, newline="\r\n" if b"\r\n" in item_before else "\n")
    upsert(pages[owner_path][1], _decisions.ledger_ref(owner_path), title="Decisions")
    candidates = [
        (ledger_member, ledger_before, ledger_after),
        *((item_page(p).rel, before, doc.serialize().encode("utf-8")) for p, (before, doc) in pages.items()),
        (draft.ref.rel, draft.before, draft.after),
    ]
    writes = tuple(
        PlannedWrite(member, hashlib.sha256(before).hexdigest() if before is not None else None, after)
        for member, before, after in candidates
        if before != after
    )
    parents = tuple(dict.fromkeys(Path(member).parent.as_posix() for member in (ledger_member, draft.ref.rel)))
    return WorkMutationPlan(
        root=root,
        operation="file",
        path_mapping=MappingProxyType({}),
        move_plan=None,
        moves=(),
        writes=writes,
        deletes=(),
        mkdirs=parents,
        warnings=plan.warnings,
        refusals=(),
        validate_paths=(path,),
        directory_preconditions=tuple(
            DirectoryPrecondition(parent, None) for parent in parents if not (root / parent).exists()
        ),
    )


def run_accept_integration(
    layout: WorkspaceLayout,
    path: str,
    *,
    repo_name: str,
    evidence: str,
    reason: str,
    by: str,
    today: date,
    apply: bool = False,
) -> AcceptIntegrationResult:
    """Plan by default; on apply write the attested receipt entry and ledger entry together."""
    reason, by = reason.strip(), by.strip()
    target: FinishTarget | None = None
    source: str | None = None
    sha: str | None = None

    def result(
        refusal: AcceptRefusal | None = None,
        detail: str = "",
        *,
        decision_id: str | None = None,
        receipt: str | None = None,
        ledger: str | None = None,
        applied: bool = False,
    ) -> AcceptIntegrationResult:
        return AcceptIntegrationResult(
            path,
            repo_name,
            target.source_branch if target else None,
            source,
            target.target_branch if target else None,
            sha,
            by,
            reason,
            decision_id,
            receipt,
            ledger,
            refusal,
            detail,
            applied,
        )

    if not reason or not by:
        return result("missing-attribution", "--reason and --by are both required and must be nonempty")
    try:
        target, source, sha = _observe(
            layout, tuple(load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))), path, repo_name, evidence
        )
    except _Refused as refused:
        return result(refused.refusal, refused.detail)
    if not apply:
        return result()
    commit_mode(layout)
    with locked_decision_owner(layout, path) as context:
        try:
            target, source, sha = _observe(layout, context.items, path, repo_name, evidence)
        except _Refused as refused:
            return result(refused.refusal, refused.detail)
        assert target.repo.name is not None
        guard = finish_read_guard(layout, path, bundle=context.bundle)
        entry = VerifiedIntegration(
            target.repo.name,
            target.source_branch,
            source,
            target.target_branch,
            sha,
            "attested",
            "accepted",
            None,
            by,
            reason,
        )
        ledger = context.owner.ledger
        ledger_before = ledger.read_bytes() if ledger.exists() else None
        plan = _decisions.plan_append(
            ledger,
            question=f"Accept {sha[:12]} as {path}'s integration into {target.target_branch} in {repo_name}?",
            status="answered",
            answer=(
                f"Accepted {sha} as {target.source_branch} ({source}) integrated into {target.target_branch}; "
                "recorded as attested evidence, not verified."
            ),
            rationale=reason,
            if_wrong=(
                f"The finish resolves on unverified evidence: reopen {path} and integrate with `gw work integrate`."
            ),
            affects=(path,),
            on=today,
            decided_by=by,
        )
        if plan.refusal is not None or plan.primary is None:
            return result("receipt-refused", f"decision ledger refused: {plan.detail}")
        draft = draft_receipt(layout, path, entry, today=today)
        ledger_rel = ledger.relative_to(context.bundle.root).as_posix()

        def validate() -> None:
            fresh = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
            try:
                current = _observe(layout, tuple(fresh), path, repo_name, sha)
            except _Refused as refused:
                raise WorkspaceError(f"integration evidence changed: {refused.detail}; inspect and retry") from None
            if finish_read_guard(layout, path) != guard or current[1] != source:
                raise WorkspaceError("finish owner, receipt, configuration or source changed; inspect and retry")

        application = apply_mutation(
            layout,
            _mutation(context, path, plan, ledger_before, draft),
            repo_roots=resolve_repos(layout),
            baseline_bundle=context.bundle,
            validate_read_set=validate,
            commit=WorkspaceCommit(
                f"workspace: accept {item_stem(path)} integration evidence for {repo_name}", items=(path,)
            ),
        )
        if not application.ok:
            return result("receipt-refused", f"transaction refused: {application}", ledger=ledger_rel)
        return result(decision_id=plan.primary.id, receipt=draft.ref.rel, ledger=ledger_rel, applied=True)


__all__ = ["AcceptIntegrationResult", "AcceptRefusal", "run_accept_integration"]

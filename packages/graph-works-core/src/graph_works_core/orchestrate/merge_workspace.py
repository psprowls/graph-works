"""Integrate a workspace branch and record evidence under owner then bundle locks.

The one sanctioned non-verb commit on workspace main; a conflict aborts and
refuses, holding the finish. Nested children integrate in their epic anchor
checkout and use finish-receipt.py record --repo _workspace instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from work_tracker_okf.items import IGNORE, WorkItem, load_items

from graph_works_core.orchestrate.finish_receipt import record_finish_in
from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.decision_owner import decision_context, locked_decision_owner
from graph_works_core.workspace.finish import FinishTarget, resolve_finish_targets
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.provenance import head_sha, probe_git, run_git
from graph_works_core.workspace.repo_context import observe_repository
from graph_works_core.workspace.transactions import held_bundle_lock
from graph_works_core.workspace.workspace_branch import WORKSPACE_REPO, workspace_repo

MergeWorkspaceRefusal = Literal[
    "disabled",
    "unknown-path",
    "not-at-finish",
    "no-workspace-target",
    "not-main-target",
    "wrong-checkout",
    "merge-failed",
    "receipt-refused",
]
_TERMINAL = frozenset({"resolved", "wontfix", "superseded"})


@dataclass(frozen=True, slots=True)
class MergeWorkspaceResult:
    path: str
    source_branch: str | None
    target_branch: str | None
    refusal: MergeWorkspaceRefusal | None
    detail: str
    merge_commit: str | None
    receipt_path: str | None
    applied: bool


def _target(
    layout: WorkspaceLayout, items: Sequence[WorkItem], path: str
) -> tuple[FinishTarget | None, MergeWorkspaceRefusal | None, str]:
    item = next((i for i in items if i.path == path), None)
    if item is None:
        return None, "unknown-path", f"unknown work item {path!r}"
    if item.phase != "finish" or item.work_status in _TERMINAL:
        return None, "not-at-finish", f"{path} is at {item.phase!r} ({item.work_status})"
    plan = resolve_finish_targets(layout, items, path)
    target = next((t for t in plan.targets if t.repo.name == WORKSPACE_REPO), None)
    if plan.blockers or target is None:
        return target, "no-workspace-target", "; ".join(plan.blockers) or f"{path} has no _workspace finish target"
    assert target.repo.path is not None
    trunk = observe_repository(target.repo.path).default_base
    if target.target_branch != trunk:
        return (
            target,
            "not-main-target",
            (
                f"{path} merges into workspace anchor {target.target_branch!r}: run `git merge {target.source_branch}` "
                "in that anchor worktree, then finish-receipt.py record --repo _workspace"
            ),
        )
    current = (run_git(target.repo.path, "branch", "--show-current") or "").strip()
    if current != target.target_branch:
        return (
            target,
            "wrong-checkout",
            f"workspace root is on {current or 'a detached HEAD'!r}, not {target.target_branch!r}",
        )
    return target, None, ""


def run_merge_workspace(
    layout: WorkspaceLayout, path: str, *, today: date, apply: bool = False
) -> MergeWorkspaceResult:
    """Plan by default; merge and record without releasing either lock on apply."""
    workspace, note = workspace_repo(layout)
    if workspace is None:
        return MergeWorkspaceResult(path, None, None, "disabled", note or "", None, None, False)
    assert workspace.path is not None
    target, refusal, detail = _target(layout, load_items(load_workspace_bundle(layout, ignore=IGNORE)), path)

    def result(
        refusal: MergeWorkspaceRefusal | None,
        detail: str = "",
        *,
        commit: str | None = None,
        receipt: str | None = None,
        applied: bool = False,
    ) -> MergeWorkspaceResult:
        return MergeWorkspaceResult(
            path,
            target.source_branch if target else None,
            target.target_branch if target else None,
            refusal,
            detail,
            commit,
            receipt,
            applied,
        )

    if refusal is not None or not apply:
        return result(refusal, detail)
    with locked_decision_owner(layout, path) as owned, held_bundle_lock(layout):
        # The bundle lock may have waited behind another writer; reload inside it.
        context = decision_context(layout, path)
        if context.owner != owned.owner:
            return result("receipt-refused", "decision owner changed; inspect and retry")
        target, refusal, detail = _target(layout, context.items, path)
        if refusal is not None:
            return result(refusal, detail)
        assert target is not None
        # Never abort another caller's merge, or incorporate unrelated staged work.
        pending = probe_git(workspace.path, "rev-parse", "-q", "--verify", "MERGE_HEAD")
        if pending.returncode != 1:
            return result("merge-failed", "workspace has an existing or unverifiable merge state; inspect it first")
        clean = probe_git(workspace.path, "status", "--porcelain")
        if clean.returncode != 0 or clean.stdout.strip():
            return result("merge-failed", "workspace checkout must be clean before merging")
        merged = probe_git(
            workspace.path,
            "merge",
            "--no-ff",
            "--no-edit",
            "-m",
            f"workspace: merge {target.source_branch} for {path}",
            f"refs/heads/{target.source_branch}",
        )
        if merged.returncode != 0:
            detail = (merged.stderr or merged.stdout).strip() or f"git merge {merged.cause}"
            if probe_git(workspace.path, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
                aborted = probe_git(workspace.path, "merge", "--abort")
                if aborted.returncode != 0:
                    detail += "; merge abort failed; inspect workspace before retrying"
            return result("merge-failed", detail)
        commit = head_sha(workspace.path)
        # Git can have changed the owner page and receipt. Never reuse preimages.
        try:
            fresh = decision_context(layout, path)
        except ValueError as exc:
            return result("receipt-refused", str(exc), commit=commit, applied=True)
        if fresh.owner != owned.owner:
            return result(
                "receipt-refused", "merged content changed decision owner; record again", commit=commit, applied=True
            )
        receipt = record_finish_in(layout, fresh, path, repo_name=WORKSPACE_REPO, today=today)
        if receipt.refusal is not None or (receipt.commit is not None and receipt.commit.status == "failed"):
            return result(
                "receipt-refused",
                f"merged at {commit}; record again: {receipt.refusal or '; '.join(receipt.warnings)}",
                commit=commit,
                receipt=receipt.receipt_path,
                applied=True,
            )
        return result(None, commit=commit, receipt=receipt.receipt_path, applied=True)


__all__ = ["MergeWorkspaceRefusal", "MergeWorkspaceResult", "run_merge_workspace"]

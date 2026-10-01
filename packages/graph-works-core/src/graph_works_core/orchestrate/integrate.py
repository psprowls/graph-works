"""Integrate one code repository's finish source into its target with an explicit strategy.

The code-repository sibling of `merge_workspace`: gw, not the finishing
worker, chooses the merge flags, so the receipt's verification always matches
how the result was made. Plans by default; only `apply=True` changes Git
(ADR 2026-08-18). `_workspace` targets keep `merge-workspace` / the anchor path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from work_tracker_okf.items import IGNORE, WorkItem, load_items

from graph_works_core.orchestrate.finish_receipt import receipt_problem, record_finish_in
from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.commits import commit_mode
from graph_works_core.workspace.decision_owner import decision_context, locked_decision_owner
from graph_works_core.workspace.finish import (
    FinishPlan,
    FinishTarget,
    IntegrationStrategy,
    VerifiedIntegration,
    discover_integration,
    finish_git,
    finish_strategy,
    finish_toolchain,
    merge_tree_unsupported,
    read_finish_receipt,
    resolve_finish_targets,
    verify_integration,
)
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.provenance import GitOutcome
from graph_works_core.workspace.workspace_branch import WORKSPACE_REPO

IntegrateRefusal = Literal[
    "unknown-path",
    "not-at-finish",
    "no-target",
    "workspace-target",
    "dirty",
    "not-fast-forward",
    "nothing-to-integrate",
    "conflict",
    "git-unavailable",
    "receipt-refused",
]
StrategySource = Literal["flag", "config", "default"]
IntegrateOutcome = Literal["planned", "integrated", "already-integrated"]
_TERMINAL = frozenset({"resolved", "wontfix", "superseded"})
#: A merge or commit runs the repository's own hooks; the 5s probe cap is too short.
_MERGE_TIMEOUT_SECONDS = 120.0
#: Another operation's state in the target worktree; never aborted by gw.
_PENDING = ("MERGE_HEAD", "SQUASH_MSG", "CHERRY_PICK_HEAD", "REVERT_HEAD")


@dataclass(frozen=True, slots=True)
class IntegrateResult:
    path: str
    repo: str
    strategy: IntegrationStrategy | None
    strategy_source: StrategySource | None
    source_branch: str | None
    source_commit: str | None
    target_branch: str | None
    target_worktree: str | None
    target_before: str | None
    result_commit: str | None
    outcome: IntegrateOutcome | None
    refusal: IntegrateRefusal | None
    detail: str
    conflicts: tuple[str, ...]
    receipt_path: str | None
    applied: bool


class _Refused(Exception):
    def __init__(
        self, refusal: IntegrateRefusal, detail: str, conflicts: tuple[str, ...] = (), *, restored: bool = False
    ) -> None:
        super().__init__(detail)
        self.refusal = refusal
        self.detail = detail
        self.conflicts = conflicts
        self.restored = restored


def _run(worktree: Path, *args: str) -> GitOutcome:
    outcome = finish_git(worktree, *args, timeout=_MERGE_TIMEOUT_SECONDS)
    if outcome.cause != "ok":
        raise _Refused("git-unavailable", f"git {args[0]}: {outcome.cause} {outcome.stderr}".strip())
    return outcome


def _output(outcome: GitOutcome) -> str:
    return (outcome.stderr or outcome.stdout).strip()


def _sha(worktree: Path, ref: str) -> str:
    resolved = _run(worktree, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    if resolved.returncode != 0:
        raise _Refused("git-unavailable", f"cannot resolve {ref} in {worktree}")
    return resolved.stdout.strip()


def _target(
    layout: WorkspaceLayout, items: Sequence[WorkItem], path: str, repo_name: str
) -> tuple[WorkItem, FinishTarget, Path, FinishPlan]:
    if repo_name == WORKSPACE_REPO:
        raise _Refused(
            "workspace-target",
            "the workspace repository integrates with `gw work merge-workspace` (main) or in its epic anchor",
        )
    item = next((i for i in items if i.path == path), None)
    if item is None:
        raise _Refused("unknown-path", f"unknown work item {path!r}")
    if item.phase != "finish" or item.work_status in _TERMINAL:
        raise _Refused("not-at-finish", f"{path} is at {item.phase!r} ({item.work_status})")
    plan = resolve_finish_targets(layout, items, path)
    if plan.blockers:
        raise _Refused("no-target", "; ".join(plan.blockers))
    target = next((t for t in plan.targets if t.repo.name == repo_name), None)
    if target is None:
        raise _Refused("no-target", f"{repo_name}: not an owned finish target of {path}")
    if target.target_worktree is None:
        raise _Refused("no-target", f"{repo_name}: {target.target_branch!r} is not checked out in any worktree")
    return item, target, Path(target.target_worktree), plan


def _require_clean(worktree: Path, target_branch: str) -> None:
    for name in _PENDING:
        located = _run(worktree, "rev-parse", "--git-path", name).stdout.strip()
        if located and (worktree / located).exists():
            raise _Refused("dirty", f"{worktree}: {name} exists; finish or abort that operation first")
    status = _run(worktree, "status", "--porcelain")
    if status.returncode != 0 or status.stdout.strip():
        raise _Refused("dirty", f"{worktree}: the target worktree must be clean before integrating")
    current = _run(worktree, "branch", "--show-current").stdout.strip()
    if current != target_branch:
        raise _Refused("no-target", f"{worktree} is on {current or 'a detached HEAD'!r}, not {target_branch!r}")


def _settled(layout: WorkspaceLayout, path: str, target: FinishTarget) -> VerifiedIntegration | None:
    """Evidence that this source already landed: the receipt's entry, else rediscovery."""
    _doc, entries, _error = read_finish_receipt(layout, path)
    previous = next((e for e in entries if e.repo == target.repo.name), None)
    if previous is not None and verify_integration(target, previous) is None:
        return previous
    found = discover_integration(target)
    return None if isinstance(found, str) else found


def _conflicts(worktree: Path) -> tuple[str, ...]:
    listed = _run(worktree, "diff", "--name-only", "--diff-filter=U")
    return tuple(sorted(line for line in listed.stdout.splitlines() if line))


def _restore(worktree: Path, before: str, *, merge_head: bool) -> str:
    """Put the target back at *before*; "" when it is, else a sentence to append to the detail."""
    if merge_head:
        finish_git(worktree, "merge", "--abort", timeout=_MERGE_TIMEOUT_SECONDS)
    else:
        # A squash leaves no MERGE_HEAD; the tree was proven clean before, so
        # resetting index and tree to HEAD loses nothing of anyone else's.
        finish_git(worktree, "reset", "--merge", timeout=_MERGE_TIMEOUT_SECONDS)
    squash_msg = finish_git(worktree, "rev-parse", "--git-path", "SQUASH_MSG").stdout.strip()
    if squash_msg:
        (worktree / squash_msg).unlink(missing_ok=True)
    head = finish_git(worktree, "rev-parse", "HEAD").stdout.strip()
    status = finish_git(worktree, "status", "--porcelain")
    if head == before and status.returncode == 0 and not status.stdout.strip():
        return ""
    return f"; restoring the target failed: inspect {worktree} before retrying"


def _merge(
    item: WorkItem, target: FinishTarget, worktree: Path, strategy: IntegrationStrategy, source: str, before: str
) -> str:
    """Integrate the observed *source* commit; on any failure the target is put back at *before*."""
    try:
        return _merge_observed(item, target, worktree, strategy, source, before)
    except _Refused as refused:
        if refused.restored:
            raise
        # A timeout or spawn failure inside `_run` can leave MERGE_HEAD, a staged squash or a lock behind.
        raise _Refused(
            refused.refusal,
            refused.detail + _restore(worktree, before, merge_head=strategy == "merge"),
            refused.conflicts,
            restored=True,
        ) from None


def _merge_observed(
    item: WorkItem, target: FinishTarget, worktree: Path, strategy: IntegrationStrategy, source: str, before: str
) -> str:
    if strategy == "ff":
        merged = _run(worktree, "merge", "--ff-only", source)
        if merged.returncode != 0:
            raise _Refused(
                "not-fast-forward", _output(merged) + _restore(worktree, before, merge_head=False), restored=True
            )
        return _sha(worktree, "HEAD")
    if strategy == "merge":
        merged = _run(
            worktree, "merge", "--no-ff", "--no-edit", "-m", f"Merge {target.source_branch} for {item.path}", source
        )
        if merged.returncode != 0:
            conflicts = _conflicts(worktree)
            detail = _output(merged) if conflicts else f"git merge refused: {_output(merged)}"
            raise _Refused(
                "conflict" if conflicts else "git-unavailable",
                detail + _restore(worktree, before, merge_head=True),
                conflicts,
                restored=True,
            )
        return _sha(worktree, "HEAD")
    squashed = _run(worktree, "merge", "--squash", source)
    if squashed.returncode != 0:
        conflicts = _conflicts(worktree)
        detail = _output(squashed) if conflicts else f"git merge --squash refused: {_output(squashed)}"
        raise _Refused(
            "conflict" if conflicts else "git-unavailable",
            detail + _restore(worktree, before, merge_head=False),
            conflicts,
            restored=True,
        )
    if _run(worktree, "diff", "--cached", "--quiet").returncode == 0:
        raise _Refused(
            "nothing-to-integrate",
            f"squashing {target.source_branch} stages nothing onto {target.target_branch}; if its work already "
            "landed another way, a human records that with `gw work accept-integration`"
            + _restore(worktree, before, merge_head=False),
            restored=True,
        )
    committed = _run(
        worktree,
        "commit",
        "-m",
        item.title or item.path,
        "-m",
        f"Squash of {target.source_branch} ({source}) for {item.path}",
    )
    if committed.returncode != 0:
        raise _Refused(
            "git-unavailable",
            f"git commit refused: {_output(committed)}" + _restore(worktree, before, merge_head=False),
            restored=True,
        )
    return _sha(worktree, "HEAD")


@finish_toolchain
def run_integrate(
    layout: WorkspaceLayout,
    path: str,
    *,
    repo_name: str,
    strategy: IntegrationStrategy | None,
    today: date,
    apply: bool = False,
) -> IntegrateResult:
    """Plan by default; on apply, merge in the target worktree and record the receipt under the owner lock."""
    chosen: IntegrationStrategy | None = strategy
    source_of: StrategySource | None = "flag" if strategy is not None else None
    target: FinishTarget | None = None
    source: str | None = None
    before: str | None = None
    result_commit: str | None = None

    def result(
        refusal: IntegrateRefusal | None = None,
        detail: str = "",
        *,
        outcome: IntegrateOutcome | None = None,
        conflicts: tuple[str, ...] = (),
        receipt: str | None = None,
        applied: bool = False,
    ) -> IntegrateResult:
        return IntegrateResult(
            path,
            repo_name,
            chosen,
            source_of,
            target.source_branch if target else None,
            source,
            target.target_branch if target else None,
            target.target_worktree if target else None,
            before,
            result_commit,
            outcome,
            refusal,
            detail,
            conflicts,
            receipt,
            applied,
        )

    def observe(items: Sequence[WorkItem]) -> tuple[WorkItem, Path, VerifiedIntegration | None]:
        nonlocal target, source, before, chosen, source_of
        item, target, worktree, plan = _target(layout, items, path, repo_name)
        problem = receipt_problem(layout, path, plan)  # read-only: refuse before Git is touched
        if problem is not None:
            raise _Refused("receipt-refused", problem)
        if chosen is None:
            chosen, source_of = finish_strategy(layout, repo_name)
        source = _sha(worktree, "refs/heads/" + target.source_branch)
        before = _sha(worktree, "refs/heads/" + target.target_branch)
        _require_clean(worktree, target.target_branch)
        settled = _settled(layout, path, target)
        if (
            settled is None
            and chosen == "ff"
            and finish_git(worktree, "merge-base", "--is-ancestor", before, source).returncode != 0
        ):
            raise _Refused("not-fast-forward", f"{target.target_branch} has diverged from {target.source_branch}")
        if settled is None and chosen == "squash":
            unsupported = merge_tree_unsupported(worktree)
            if unsupported is not None:
                raise _Refused("git-unavailable", unsupported)
        return item, worktree, settled

    try:
        _item, _worktree, settled = observe(load_items(load_workspace_bundle(layout, ignore=IGNORE)))
    except _Refused as refused:
        return result(refused.refusal, refused.detail, conflicts=refused.conflicts)
    if not apply:
        if settled is not None:
            result_commit = settled.result_commit
        return result(outcome="already-integrated" if settled is not None else "planned")
    commit_mode(layout)  # validate workflow.workspace_commits before touching Git, as run_record_finish does
    with locked_decision_owner(layout, path) as context:
        try:
            item, worktree, settled = observe(context.items)
            assert chosen is not None and target is not None and source is not None and before is not None
            if settled is None:
                result_commit = _merge(item, target, worktree, chosen, source, before)
                assert target.repo.name is not None
                evidence: VerifiedIntegration | None = VerifiedIntegration(
                    target.repo.name,
                    target.source_branch,
                    source,
                    target.target_branch,
                    result_commit,
                    chosen,
                    "verified",
                    before,
                )
                outcome: IntegrateOutcome = "integrated"
            else:
                result_commit, evidence, outcome = settled.result_commit, None, "already-integrated"
        except _Refused as refused:
            return result(refused.refusal, refused.detail, conflicts=refused.conflicts)
        fresh = context
        if outcome == "integrated":
            # The merge can change the owner page or receipt (a workspace inside the code repository).
            try:
                fresh = decision_context(layout, path)
            except ValueError as exc:
                return result("receipt-refused", str(exc), outcome=outcome, applied=True)
            if fresh.owner != context.owner:
                return result(
                    "receipt-refused",
                    f"integrated at {result_commit}; merged content changed the decision owner; record again",
                    outcome=outcome,
                    applied=True,
                )
        receipt = record_finish_in(layout, fresh, path, repo_name=repo_name, today=today, evidence=evidence)
        if receipt.refusal is not None or (receipt.commit is not None and receipt.commit.status == "failed"):
            return result(
                "receipt-refused",
                f"integrated at {result_commit}; record it with `finish-receipt.py record --repo {repo_name}`: "
                + (receipt.refusal or "; ".join(receipt.warnings)),
                outcome=outcome,
                receipt=receipt.receipt_path,
                applied=outcome == "integrated",
            )
        return result(outcome=outcome, receipt=receipt.receipt_path, applied=True)


__all__ = ["IntegrateOutcome", "IntegrateRefusal", "IntegrateResult", "StrategySource", "run_integrate"]

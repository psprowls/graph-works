"""Repository-serialized worktree placement over resolved workspace paths."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from gw_kernel.workspace import provenance
from gw_kernel.workspace.bundle_lock import commit_pending
from gw_kernel.workspace.commits import CommitOutcome, WorkspaceCommit, item_stem
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.provenance import GitExecutable, GitFailure, strict_dirty_paths, strict_git
from gw_kernel.workspace.repos import resolve_repos
from okf_ext.locking import locked
from okf_io import Bundle, parse
from work_tracker_okf.items import WorkItem
from work_tracker_okf.mutation import PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref, item_page
from work_tracker_okf.placement import BaselinePlan, apply_baseline, plan_baseline
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

from gw_pipeline.decision_owner import locked_decision_owner
from gw_pipeline.finish import read_finish_receipt
from gw_pipeline.placement import Placement, PlacementEvent, PlacementNotifier
from gw_pipeline.repo_context import RepositoryContext, observe_branch_tips, observe_inventory, observe_repository
from gw_pipeline.transactions import MutationApplication, apply_mutation

PLACE_TIMEOUT_SECONDS = 120
PlaceMode = Literal["ensure", "start", "remove", "prune"]
StepAction = Literal["created", "adopted", "reattached", "verified", "removed", "kept", "skipped"]
PruneReason = Literal["dirty", "unmerged", "open", "no-receipt", "not-archived", "occupied"]
RefusalKind = Literal[
    "unknown-item",
    "git-unavailable",
    "parent-unresolved",
    "foreign-checkout",
    "occupied",
    "dirty-worktree",
    "refresh-conflict",
    "timeout",
    "wrong-phase",
    "already-started",
    "not-finished",
]


@dataclass(frozen=True, slots=True)
class PlaceRefusal:
    kind: RefusalKind
    detail: str


@dataclass(frozen=True, slots=True)
class Refresh:
    kind: Literal["ff", "merge", "none", "skipped", "conflict"]
    from_sha: str | None
    to_sha: str | None
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlaceStep:
    placement: Placement
    action: StepAction
    refresh: Refresh | None
    orca: str | None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class PruneKept:
    path: str
    repo: str
    branch: str
    why: PruneReason


@dataclass(frozen=True, slots=True)
class PruneCandidate:
    item: WorkItem
    placement: Placement


@dataclass(frozen=True, slots=True)
class PruneReport:
    removed_worktrees: tuple[str, ...]
    deleted_branches: tuple[str, ...]
    kept: tuple[PruneKept, ...]


@dataclass(frozen=True, slots=True)
class PlaceResult:
    path: str | None
    mode: PlaceMode
    dry_run: bool
    placement: Placement | None
    chain: tuple[PlaceStep, ...]
    start_sha: tuple[str | None, str | None] | None
    workspace_commit: CommitOutcome | None
    dirty_workspace_paths: tuple[str, ...]
    pruned: PruneReport | None
    refusal: PlaceRefusal | None
    warnings: tuple[str, ...]
    application: MutationApplication | None = None

    @property
    def ok(self) -> bool:
        return self.refusal is None and (self.application is None or self.application.ok)


@dataclass(frozen=True, slots=True)
class PlaceContext:
    layout: WorkspaceLayout
    git: GitExecutable
    notifier: PlacementNotifier
    today: date
    dry_run: bool = False
    occupied: Callable[[Path], str | None] | None = None


class _GitReadFailure(Exception):
    """Carry failed strict evidence to the public engine's refusal boundary."""


@dataclass(frozen=True, slots=True)
class _BranchTip:
    """A real commit or a predicted tree with the same reachable real ancestors."""

    commit: str | None
    treeish: str
    frontier: tuple[str, ...]

    @classmethod
    def real(cls, sha: str) -> _BranchTip:
        return cls(sha, sha, (sha,))


@dataclass(frozen=True, slots=True)
class _RefreshEvidence:
    kind: Literal["none", "ff", "merge"]
    head: str
    parent: _BranchTip
    parent_ref: str


@dataclass(frozen=True, slots=True)
class _RefreshPrediction:
    refresh: Refresh
    tip: _BranchTip | None


def _checkout_at(
    worktree: Path, identity: str, git: GitExecutable
) -> tuple[Literal["absent", "match", "foreign"], str]:
    """Prove directory identity and return its branch for the caller to compare."""
    if not worktree.exists() and not worktree.is_symlink():
        return "absent", ""
    if not worktree.is_dir() or not (worktree / ".git").exists():
        return "foreign", "plain directory or file"
    common = strict_git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir", git=git)
    # Quiet makes detached HEAD an exit-1 state, distinct from a read failure.
    branch = provenance.probe_git(worktree, "symbolic-ref", "--quiet", "HEAD", executable=git.path)
    if isinstance(common, GitFailure):
        raise _GitReadFailure(common.detail)
    if str(Path(common.strip()).resolve()) != identity:
        return "foreign", "another repository"
    if branch.cause == "ok" and branch.returncode == 1:
        return "foreign", "detached HEAD"
    if branch.returncode != 0:
        raise _GitReadFailure(f"git symbolic-ref in {worktree} failed: {branch.stderr or branch.cause}")
    return "match", branch.stdout.strip().removeprefix("refs/heads/")


def _mutate(cwd: Path, *args: str, git: GitExecutable) -> str | PlaceRefusal:
    """Bound a Git mutation, pruning stale worktree registration on failure."""
    outcome = strict_git(cwd, *args, git=git, timeout=PLACE_TIMEOUT_SECONDS)
    if (
        isinstance(outcome, GitFailure)
        and args[:2] == ("worktree", "add")
        and outcome.cause != "timeout"
        and any(text in outcome.detail.lower() for text in ("already registered", "already exists"))
    ):
        strict_git(cwd, "worktree", "prune", git=git)
        outcome = strict_git(cwd, *args, git=git, timeout=PLACE_TIMEOUT_SECONDS)
    if isinstance(outcome, GitFailure):
        if outcome.cause == "timeout":
            strict_git(cwd, "worktree", "prune", git=git)
        return PlaceRefusal("timeout" if outcome.cause == "timeout" else "git-unavailable", outcome.detail)
    return outcome


def _failed_refresh(worktree: Path, placement: Placement, git: GitExecutable, failure: PlaceRefusal) -> PlaceRefusal:
    """Abort an in-progress merge and prove cleanup before reporting a conflict."""
    conflicts = strict_git(worktree, "diff", "--name-only", "--diff-filter=U", git=git)
    merge_head = provenance.probe_git(worktree, "rev-parse", "--verify", "--quiet", "MERGE_HEAD", executable=git.path)
    cleanup_error = None
    if merge_head.cause != "ok" or merge_head.returncode not in (0, 1):
        cleanup_error = f"cannot read merge state at {worktree}: {merge_head.stderr or merge_head.cause}"
    elif merge_head.returncode == 0:
        aborted = _mutate(worktree, "merge", "--abort", git=git)
        if isinstance(aborted, PlaceRefusal):
            cleanup_error = aborted.detail
    dirty = strict_dirty_paths(worktree, (), git=git)
    if isinstance(dirty, GitFailure):
        cleanup_error = dirty.detail
    elif dirty:
        cleanup_error = f"merge cleanup left changes at {worktree}: {', '.join(dirty)}"
    if cleanup_error is not None:
        return PlaceRefusal(
            "timeout" if failure.kind == "timeout" else "git-unavailable",
            f"{failure.detail}; {cleanup_error}; inspect the worktree before rerunning gw work place",
        )
    if failure.kind == "timeout":
        return failure
    if isinstance(conflicts, GitFailure):
        return PlaceRefusal("git-unavailable", conflicts.detail)
    paths = tuple(conflicts.splitlines())
    if paths:
        return PlaceRefusal(
            "refresh-conflict",
            f"merging {placement.parent_branch} into {placement.branch} at {worktree} "
            f"conflicted in {', '.join(paths)}; resolve the merge in the worktree and rerun gw work place",
        )
    return failure


def _classify_refresh(
    worktree: Path,
    placement: Placement,
    git: GitExecutable,
    *,
    head_ref: str = "HEAD",
    parent_ref: str | None = None,
    predicted_parent: _BranchTip | None = None,
    check_dirty: bool = True,
    optional_locks: bool = True,
) -> _RefreshEvidence | PlaceRefusal:
    """Share strict evidence and history classification between live and dry runs."""
    if check_dirty:
        dirty = strict_dirty_paths(worktree, (), git=git, optional_locks=optional_locks)
        if isinstance(dirty, GitFailure):
            return PlaceRefusal("git-unavailable", dirty.detail)
        if dirty:
            return PlaceRefusal(
                "dirty-worktree",
                f"{placement.worktree} on {placement.branch} has uncommitted changes: {', '.join(dirty)}; "
                "commit or stash them and rerun gw work place",
            )
    parent_ref = parent_ref or f"refs/heads/{placement.parent_branch}"
    if predicted_parent is None:
        parent = provenance.strict_commit(worktree, parent_ref, git=git)
        if isinstance(parent, GitFailure):
            return PlaceRefusal("parent-unresolved" if parent.cause == "nonzero" else "git-unavailable", parent.detail)
        parent_tip = _BranchTip.real(parent)
    else:
        parent_tip = predicted_parent
        parent_ref = parent_tip.treeish
    head = provenance.strict_commit(worktree, head_ref, git=git)
    if isinstance(head, GitFailure):
        return PlaceRefusal("git-unavailable", head.detail)
    if parent_tip.commit is not None:
        ancestor = provenance.strict_is_ancestor(worktree, parent_tip.commit, head, git=git)
        if isinstance(ancestor, GitFailure):
            return PlaceRefusal("git-unavailable", ancestor.detail)
        if ancestor:
            return _RefreshEvidence("none", head, parent_tip, parent_ref)
        count_refs: tuple[str, ...] = (f"{parent_ref}..{head_ref}",)
    else:
        # An unperformed merge cannot already be an ancestor of a real HEAD.
        # Its reachable real history is the union of its parents' frontiers.
        count_refs = (head_ref, "--not", *parent_tip.frontier)
    count = strict_git(worktree, "rev-list", "--count", *count_refs, git=git)
    if isinstance(count, GitFailure):
        return PlaceRefusal("git-unavailable", count.detail)
    kind: Literal["ff", "merge"] = "ff" if count.strip() == "0" else "merge"
    return _RefreshEvidence(kind, head, parent_tip, parent_ref)


def _refresh(worktree: Path, placement: Placement, git: GitExecutable) -> Refresh | PlaceRefusal:
    """Bring one clean branch up to its freshly refreshed local parent."""
    evidence = _classify_refresh(worktree, placement, git)
    if isinstance(evidence, PlaceRefusal):
        return evidence
    if evidence.kind == "none":
        return Refresh("none", evidence.head, evidence.head)
    args = (
        ("--ff-only", evidence.parent_ref)
        if evidence.kind == "ff"
        else (
            "--no-ff",
            "--no-edit",
            "-m",
            f"Merge {placement.parent_branch} into {placement.branch}",
            evidence.parent_ref,
        )
    )
    outcome = _mutate(worktree, "merge", *args, git=git)
    if isinstance(outcome, PlaceRefusal):
        return _failed_refresh(worktree, placement, git, outcome)
    tip = provenance.strict_commit(worktree, "HEAD", git=git)
    return (
        PlaceRefusal("git-unavailable", tip.detail)
        if isinstance(tip, GitFailure)
        else Refresh(evidence.kind, evidence.head, tip)
    )


def _predict_refresh(
    worktree: Path,
    placement: Placement,
    git: GitExecutable,
    *,
    head_ref: str = "HEAD",
    parent_ref: str | None = None,
    predicted_parent: _BranchTip | None = None,
    check_dirty: bool = True,
) -> _RefreshPrediction | PlaceRefusal:
    """Carry predicted merge trees and real ancestry without inventing commits."""
    evidence = _classify_refresh(
        worktree,
        placement,
        git,
        head_ref=head_ref,
        parent_ref=parent_ref,
        predicted_parent=predicted_parent,
        check_dirty=check_dirty,
        optional_locks=False,
    )
    if isinstance(evidence, PlaceRefusal):
        return evidence
    head, parent = evidence.head, evidence.parent
    if evidence.kind == "none":
        return _RefreshPrediction(Refresh("none", head, head), _BranchTip.real(head))
    if evidence.kind == "ff":
        return _RefreshPrediction(Refresh("ff", head, parent.commit), parent)
    merge_base: tuple[str, ...] = ()
    if parent.commit is None:
        bases = strict_git(worktree, "merge-base", "--all", head, *parent.frontier, git=git)
        if isinstance(bases, GitFailure):
            return PlaceRefusal("git-unavailable", bases.detail)
        candidates = bases.splitlines()
        if len(candidates) != 1:
            why = "multiple merge bases" if candidates else "no merge base"
            return PlaceRefusal(
                "git-unavailable",
                f"cannot predict refresh of {placement.branch} at {placement.worktree}: {why}; "
                "run gw work place without --dry-run so Git can resolve the merge ancestry",
            )
        merge_base = (f"--merge-base={candidates[0]}",)
    merged = provenance.probe_git(
        worktree,
        "merge-tree",
        "--write-tree",
        "--name-only",
        "--no-messages",
        *merge_base,
        head_ref,
        evidence.parent_ref,
        executable=git.path,
    )
    if merged.cause != "ok" or merged.returncode not in (0, 1):
        return PlaceRefusal(
            "git-unavailable",
            f"git merge-tree in {worktree} failed ({merged.cause}, exit {merged.returncode}): {merged.stderr.strip()}",
        )
    if merged.returncode == 1:
        return _RefreshPrediction(
            Refresh("conflict", head, parent.commit, tuple(name for name in merged.stdout.splitlines()[1:] if name)),
            None,
        )
    tree = merged.stdout.splitlines()[0] if merged.stdout else ""
    if not tree:
        return PlaceRefusal("git-unavailable", f"git merge-tree in {worktree} returned no predicted tree")
    return _RefreshPrediction(
        Refresh("merge", head, parent.commit),
        _BranchTip(None, tree, tuple(dict.fromkeys((head, *parent.frontier)))),
    )


def _workspace_state(
    ctx: PlaceContext, path: str | None
) -> tuple[CommitOutcome | None, tuple[str, ...], PlaceRefusal | None]:
    commit = (
        None
        if ctx.dry_run or path is None
        else commit_pending(
            ctx.layout,
            WorkspaceCommit(
                f"workspace: place {item_stem(path)}", items=(path,), extra_paths=(item_page(path).rel, path)
            ),
        )
    )
    if commit is not None and commit.status == "failed":
        return commit, (), PlaceRefusal("git-unavailable", commit.reason or "workspace commit failed")
    dirty = strict_dirty_paths(ctx.layout.root, (), git=ctx.git, optional_locks=not ctx.dry_run)
    if isinstance(dirty, GitFailure):
        if "not a git repository" in dirty.detail.lower():
            return commit, (), None
        return commit, (), PlaceRefusal("git-unavailable", dirty.detail)
    if not dirty or path is None:
        return commit, dirty, None
    toplevel = strict_git(ctx.layout.root, "rev-parse", "--show-toplevel", git=ctx.git)
    if isinstance(toplevel, GitFailure):
        return commit, (), PlaceRefusal("git-unavailable", toplevel.detail)
    root = Path(toplevel.strip())
    own = (ctx.layout.bundle_dir / path, item_page(path).path(ctx.layout.bundle_dir))
    unrelated = tuple(
        name
        for name in dirty
        if not any((root / name) == member or (root / name).is_relative_to(member) for member in own)
    )
    return commit, unrelated, None


def _occupied(ctx: PlaceContext, path: Path) -> str | None:
    dirty = strict_dirty_paths(path, (), git=ctx.git, optional_locks=not ctx.dry_run)
    if isinstance(dirty, GitFailure):
        raise _GitReadFailure(dirty.detail)
    if dirty:
        return "dirty"
    return ctx.occupied(path) if ctx.occupied is not None else None


def _ensure_step(
    ctx: PlaceContext,
    placement: Placement,
    observed: RepositoryContext,
    tips: dict[str, _BranchTip],
    *,
    verify_only: bool = False,
) -> tuple[PlaceStep, PlaceRefusal | None]:
    state, detail = _checkout_at(placement.worktree, observed.identity, ctx.git)
    action: StepAction = "verified"
    args: tuple[str, ...] = ()
    tip = tips.get(placement.branch)
    refusal = None
    if state == "absent" and verify_only:
        refusal = PlaceRefusal(
            "foreign-checkout",
            f"{placement.worktree}: no worktree for {placement.branch} exists there; verify-only never creates one; "
            "rerun gw work place",
        )
    elif state == "foreign" or (state == "match" and detail != placement.branch):
        refusal = PlaceRefusal(
            "foreign-checkout",
            f"{placement.worktree}: expected {placement.branch}; found {detail}; move it aside and rerun",
        )
    elif state == "absent":
        legacy = observed.inventory.get(placement.branch, ())
        if legacy:
            there = Path(legacy[0])
            occupied = _occupied(ctx, there)
            if occupied is not None:
                refusal = PlaceRefusal(
                    "occupied", f"{there} holds {placement.branch}: {occupied}; release it and rerun"
                )
            else:
                action, args = "adopted", ("worktree", "move", str(there), str(placement.worktree))
        elif tip is not None:
            action, args = "reattached", ("worktree", "add", str(placement.worktree), placement.branch)
        else:
            tip = tips.get(placement.parent_branch)
            if tip is None:
                refusal = PlaceRefusal(
                    "parent-unresolved",
                    f"{placement.worktree}: cannot create {placement.branch}; "
                    f"local parent {placement.parent_branch} has no tip",
                )
            else:
                action = "created"
                if not ctx.dry_run:
                    args = ("worktree", "add", "-b", placement.branch, str(placement.worktree), tip.treeish)
    if refusal is None and args and not ctx.dry_run:
        placement.worktree.parent.mkdir(parents=True, exist_ok=True)
        outcome = _mutate(placement.repo_root, *args, git=ctx.git)
        if isinstance(outcome, PlaceRefusal):
            refusal = outcome
    if refusal is not None:
        return PlaceStep(placement, "skipped", None, None, refusal.detail), refusal
    if tip is not None:
        tips[placement.branch] = tip
    note = None
    if action != "verified" and not ctx.dry_run:
        try:
            note = ctx.notifier.placed(
                PlacementEvent(
                    placement.repo_root,
                    placement.worktree,
                    placement.parent_worktree,
                    placement.slug,
                    ctx.layout.worktrees_dir,
                )
            )
        except Exception as exc:
            note = f"orca: notifier failed ({exc})"
    sha = tip.commit if tip is not None else None
    return PlaceStep(placement, action, Refresh("none", sha, sha), note), None


def _predict_step_refresh(
    ctx: PlaceContext, step: PlaceStep, observed: RepositoryContext, tips: dict[str, _BranchTip]
) -> Refresh | PlaceRefusal:
    member = step.placement
    source = member.worktree
    head_ref = "HEAD"
    if step.action == "adopted":
        source = Path(observed.inventory[member.branch][0])
    elif step.action == "reattached":
        source = member.repo_root
        head_ref = f"refs/heads/{member.branch}"
    parent_tip = tips.get(member.parent_branch)
    simulated_parent = (
        parent_tip.commit
        if parent_tip is not None and parent_tip.commit != observed.branch_tips.get(member.parent_branch)
        else None
    )
    prediction = _predict_refresh(
        source,
        member,
        ctx.git,
        head_ref=head_ref,
        parent_ref=simulated_parent,
        predicted_parent=parent_tip if parent_tip is not None and parent_tip.commit is None else None,
        check_dirty=step.action != "reattached",
    )
    if isinstance(prediction, PlaceRefusal):
        return prediction
    if prediction.tip is not None:
        tips[member.branch] = prediction.tip
    return prediction.refresh


def _ensure(
    ctx: PlaceContext, chain: tuple[Placement, ...], *, refresh: bool = True
) -> tuple[PlaceResult, dict[str, _BranchTip]]:
    """Retain private predicted history for the execute baseline caller."""
    placement = chain[-1] if chain else None
    steps: list[PlaceStep] = []
    commit = None
    dirty: tuple[str, ...] = ()
    refusal = None
    tips: dict[str, _BranchTip] = {}
    if placement is not None:
        try:
            with locked(ctx.layout.cache_dir / "placement" / f"{placement.repo_name}.lock"):
                commit, dirty, refusal = _workspace_state(ctx, placement.path)
                if refusal is not None:
                    steps.extend(PlaceStep(member, "skipped", None, None) for member in chain)
                else:
                    observed = observe_repository(
                        placement.repo_root,
                        paths=[p.worktree for p in chain],
                        git=ctx.git,
                        optional_locks=not ctx.dry_run,
                    )
                    if not (observed.identity_known and observed.inventory_known and observed.branch_tips_known):
                        refusal = PlaceRefusal(
                            "git-unavailable",
                            f"cannot observe local repository {placement.repo_root}; check git and rerun",
                        )
                    tips = {branch: _BranchTip.real(sha) for branch, sha in observed.branch_tips.items()}
                    for member in chain:
                        if refusal is not None:
                            steps.append(PlaceStep(member, "skipped", None, None))
                            continue
                        try:
                            with locked_decision_owner(ctx.layout, member.path):
                                step, refusal = _ensure_step(ctx, member, observed, tips, verify_only=not refresh)
                                if refusal is None and refresh and step.action != "created":
                                    refreshed = (
                                        _predict_step_refresh(ctx, step, observed, tips)
                                        if ctx.dry_run
                                        else _refresh(member.worktree, member, ctx.git)
                                    )
                                    if isinstance(refreshed, PlaceRefusal):
                                        refusal = refreshed
                                        step = replace(step, refresh=None, detail=refusal.detail)
                                    else:
                                        step = replace(step, refresh=refreshed)
                                        if refreshed.kind == "conflict":
                                            refusal = PlaceRefusal(
                                                "refresh-conflict",
                                                f"merging {member.parent_branch} into {member.branch} "
                                                f"at {member.worktree} conflicted in {', '.join(refreshed.conflicts)}; "
                                                "resolve the merge in the worktree and rerun gw work place",
                                            )
                                            step = replace(step, detail=refusal.detail)
                                        elif not ctx.dry_run and refreshed.to_sha is not None:
                                            tips[member.branch] = _BranchTip.real(refreshed.to_sha)
                        except (OSError, ValueError, _GitReadFailure) as exc:
                            refusal = PlaceRefusal(
                                "unknown-item" if isinstance(exc, ValueError) else "git-unavailable", str(exc)
                            )
                            step = PlaceStep(member, "skipped", None, None, refusal.detail)
                        steps.append(step)
        except OSError as exc:
            refusal = refusal or PlaceRefusal("git-unavailable", str(exc))
            steps.extend(PlaceStep(member, "skipped", None, None) for member in chain[len(steps) :])
    result = PlaceResult(
        placement.path if placement is not None else None,
        "ensure",
        ctx.dry_run,
        placement,
        tuple(steps),
        None,
        commit,
        dirty,
        None,
        refusal,
        (),
    )
    return result, tips


def ensure(ctx: PlaceContext, chain: tuple[Placement, ...], *, refresh: bool = True) -> PlaceResult:
    """Ensure owners outermost first, then the called item's checkout.

    `refresh=False` is verify-only: it checks the computed path, branch and repository, refuses a missing
    checkout as `foreign-checkout` (it never creates, adopts or reattaches one), and skips the parent refresh and its
    dirty-worktree refusal.
    """
    return _ensure(ctx, chain, refresh=refresh)[0]


def _receipt_names_tip(layout: WorkspaceLayout, path: str, repo_name: str, tip: str) -> bool | str:
    """Trust the resolving advance's verified or accepted receipt without revalidating Git strategy."""
    _, entries, error = read_finish_receipt(layout, path)
    if error is not None:
        return error
    return any(
        entry.repo == repo_name and entry.source_commit == tip and entry.evidence in {"verified", "accepted"}
        for entry in entries
    )


def _remove_step(ctx: PlaceContext, placement: Placement, observed: RepositoryContext) -> PlaceStep | PlaceRefusal:
    state, branch = _checkout_at(placement.worktree, observed.identity, ctx.git)
    if state == "absent":
        return PlaceStep(placement, "kept", None, None, "absent")
    if state == "foreign" or branch != placement.branch:
        return PlaceRefusal(
            "foreign-checkout",
            f"{placement.worktree}: expected {placement.branch}; found {branch}; move it aside and rerun",
        )
    tip = observed.branch_tips.get(placement.branch)
    if tip is None:
        return PlaceRefusal(
            "git-unavailable", f"cannot read local tip of {placement.branch} at {placement.worktree}; inspect and rerun"
        )
    receipt = _receipt_names_tip(ctx.layout, placement.path, placement.repo_name, tip)
    if receipt is not True:
        receipt_path = artifact_ref(placement.path, MANAGED_ARTIFACTS["finish-receipt"]).path(ctx.layout.bundle_dir)
        return PlaceRefusal(
            "not-finished",
            f"{placement.worktree} on {placement.branch} at {tip} has no matching verified or accepted entry "
            f"in {receipt_path}{': ' + receipt if isinstance(receipt, str) else ''}; "
            "finish integration and record its receipt before removing the worktree",
        )
    dirty = strict_dirty_paths(placement.worktree, (), git=ctx.git, optional_locks=not ctx.dry_run)
    if isinstance(dirty, GitFailure):
        return PlaceRefusal("git-unavailable", dirty.detail)
    if dirty:
        return PlaceRefusal(
            "dirty-worktree",
            f"{placement.worktree} on {placement.branch} has uncommitted changes: {', '.join(dirty)}; "
            "commit or stash them and rerun gw work place --remove",
        )
    if not ctx.dry_run:
        outcome = _mutate(placement.repo_root, "worktree", "remove", str(placement.worktree), git=ctx.git)
        if isinstance(outcome, PlaceRefusal):
            return outcome
    return PlaceStep(placement, "removed", None, None)


def remove(ctx: PlaceContext, placement: Placement) -> PlaceResult:
    """Remove only the computed clean checkout whose current tip the finish receipt names."""
    commit = None
    dirty: tuple[str, ...] = ()
    refusal = None
    step = PlaceStep(placement, "skipped", None, None)
    try:
        with locked(ctx.layout.cache_dir / "placement" / f"{placement.repo_name}.lock"):
            commit, dirty, refusal = _workspace_state(ctx, placement.path)
            if refusal is None:
                observed = observe_repository(
                    placement.repo_root,
                    paths=(placement.worktree,),
                    git=ctx.git,
                    optional_locks=not ctx.dry_run,
                )
                if not (observed.identity_known and observed.inventory_known and observed.branch_tips_known):
                    refusal = PlaceRefusal(
                        "git-unavailable", f"cannot observe local repository {placement.repo_root}; check git and rerun"
                    )
                else:
                    outcome = _remove_step(ctx, placement, observed)
                    if isinstance(outcome, PlaceRefusal):
                        refusal = outcome
                    else:
                        step = outcome
    except (OSError, ValueError, _GitReadFailure) as exc:
        refusal = PlaceRefusal("unknown-item" if isinstance(exc, ValueError) else "git-unavailable", str(exc))
    if refusal is not None:
        step = replace(step, detail=refusal.detail)
    return PlaceResult(
        placement.path, "remove", ctx.dry_run, placement, (step,), None, commit, dirty, None, refusal, ()
    )


def _prune_observation(ctx: PlaceContext, members: Sequence[PruneCandidate]) -> RepositoryContext | PlaceRefusal:
    placement = members[0].placement
    try:
        observed = observe_repository(
            placement.repo_root,
            paths=tuple(member.placement.worktree for member in members),
            git=ctx.git,
            optional_locks=not ctx.dry_run,
        )
    except OSError as exc:
        return PlaceRefusal("git-unavailable", str(exc))
    if not (observed.identity_known and observed.inventory_known and observed.branch_tips_known):
        return PlaceRefusal(
            "git-unavailable", f"cannot observe local repository {placement.repo_root}; check git and rerun"
        )
    return observed


def _prune_step(
    ctx: PlaceContext, candidate: PruneCandidate, observed: RepositoryContext, report: PruneReport
) -> tuple[PruneReport, PlaceRefusal | None]:
    placement, item = candidate.placement, candidate.item

    def keep(why: PruneReason) -> tuple[PruneReport, None]:
        row = PruneKept(item.path, placement.repo_name, placement.branch, why)
        return replace(report, kept=(*report.kept, row)), None

    state, _ = _checkout_at(placement.worktree, observed.identity, ctx.git)
    tip = observed.branch_tips.get(placement.branch)
    if state == "absent" and tip is None:
        return report, None
    if item.work_status not in TERMINAL_STATUSES:
        return keep("open")
    if item.work_status != "resolved":
        return keep("no-receipt")
    if state != "absent":
        outcome = _remove_step(ctx, placement, observed)
        if isinstance(outcome, PlaceRefusal):
            if outcome.kind == "dirty-worktree":
                return keep("dirty")
            if outcome.kind == "not-finished":
                return keep("no-receipt")
            return report, outcome
        report = replace(report, removed_worktrees=(*report.removed_worktrees, str(placement.worktree)))
    if tip is None:
        return report, None
    if not item.archived:
        receipt = _receipt_names_tip(ctx.layout, item.path, placement.repo_name, tip)
        return keep("not-archived" if receipt is True else "no-receipt")

    # Fresh destructive-check evidence never repeats the repository's status scan.
    try:
        inventory = observe_inventory(placement.repo_root, git=ctx.git)
        tips = observe_branch_tips(placement.repo_root, git=ctx.git)
    except OSError as exc:
        return report, PlaceRefusal("git-unavailable", str(exc))
    if inventory is None or tips is None:
        return report, PlaceRefusal(
            "git-unavailable",
            f"cannot observe branch holders and local tips at {placement.repo_root}; check git and rerun",
        )
    tip = tips.get(placement.branch)
    if tip is None:
        return report, None
    holders = inventory.get(placement.branch, ())
    if ctx.dry_run:
        predicted_removed = {str(Path(path).resolve()) for path in report.removed_worktrees}
        holders = tuple(path for path in holders if path not in predicted_removed)
    if holders:
        return keep("occupied")
    if _receipt_names_tip(ctx.layout, item.path, placement.repo_name, tip) is not True:
        return keep("unmerged")
    if not ctx.dry_run:
        deleted = _mutate(placement.repo_root, "branch", "-D", placement.branch, git=ctx.git)
        if isinstance(deleted, PlaceRefusal):
            return report, deleted
    return replace(report, deleted_branches=(*report.deleted_branches, placement.branch)), None


def prune(ctx: PlaceContext, candidates: Sequence[PruneCandidate], *, path: str | None = None) -> PlaceResult:
    """Sweep descendant-first candidates under one repository lock at a time."""
    report = PruneReport((), (), ())
    commit = None
    dirty: tuple[str, ...] = ()
    refusal = None
    repositories: dict[str, list[PruneCandidate]] = {}
    for candidate in candidates:
        repositories.setdefault(candidate.placement.repo_name, []).append(candidate)
    try:
        commit, dirty, refusal = _workspace_state(ctx, path)
        if refusal is None:
            for repo_name, members in repositories.items():
                with locked(ctx.layout.cache_dir / "placement" / f"{repo_name}.lock"):
                    observed = _prune_observation(ctx, members)
                    if isinstance(observed, PlaceRefusal):
                        refusal = observed
                        break
                    for candidate in members:
                        report, refusal = _prune_step(ctx, candidate, observed, report)
                        if refusal is not None:
                            break
                if refusal is not None:
                    break
    except (OSError, ValueError, _GitReadFailure) as exc:
        refusal = PlaceRefusal("unknown-item" if isinstance(exc, ValueError) else "git-unavailable", str(exc))
    placement = next((row.placement for row in candidates if row.item.path == path), None)
    return PlaceResult(path, "prune", ctx.dry_run, placement, (), None, commit, dirty, report, refusal, ())


def _baseline_mutation(bundle: Bundle, plan: BaselinePlan) -> WorkMutationPlan:
    member = item_page(plan.path).rel
    before = bundle.concepts[plan.path].serialize().encode("utf-8")
    document = parse(before.decode("utf-8"), path=bundle.root / member)
    apply_baseline(document, plan)
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


def _baseline_ancestry(
    ctx: PlaceContext, placement: Placement, recorded: str | None, tip: _BranchTip
) -> bool | GitFailure:
    """A predicted merge reaches the union of its real parent histories."""
    if recorded is None or recorded == tip.commit:
        return False
    # Dry runs may create, move or reattach a checkout only hypothetically.
    source = placement.repo_root if ctx.dry_run else placement.worktree
    for ancestor in tip.frontier:
        answer = provenance.strict_is_ancestor(source, recorded, ancestor, git=ctx.git)
        if isinstance(answer, GitFailure) or answer:
            return answer
    return False


def start(ctx: PlaceContext, chain: tuple[Placement, ...]) -> PlaceResult:
    """Ensure and refresh the checkout, then record its execute entry baseline."""
    ensured, tips = _ensure(ctx, chain)
    result = replace(ensured, mode="start")
    placement = result.placement
    if result.refusal is not None or placement is None:
        return result
    try:
        # The repository lock has been released; item writes retain the same
        # decision-owner -> transaction lock order as every other work verb.
        with locked_decision_owner(ctx.layout, placement.path) as context:
            item = context.items.by_path[placement.path]
            if item.phase != "execute":
                return replace(
                    result,
                    refusal=PlaceRefusal("wrong-phase", f"{item.path} is at phase {item.phase!r}; expected execute"),
                )
            if ctx.dry_run:
                tip = tips[placement.branch]
            else:
                head = provenance.strict_commit(placement.worktree, "HEAD", git=ctx.git)
                if isinstance(head, GitFailure):
                    return replace(result, refusal=PlaceRefusal("git-unavailable", head.detail))
                tip = _BranchTip.real(head)
            descends = _baseline_ancestry(ctx, placement, item.start_sha, tip)
            if isinstance(descends, GitFailure):
                return replace(result, refusal=PlaceRefusal("git-unavailable", descends.detail))
            plan = plan_baseline(
                context.items,
                placement.path,
                # A hypothetical merge has no commit OID. A real parent lets
                # the pure planner validate the item, but is never reported or
                # written as that merge's baseline.
                observed_head=tip.commit or tip.frontier[0],
                head_descends_from_recorded=descends,
                today=ctx.today,
            )
            if plan.refusal is not None:
                kind: RefusalKind = "git-unavailable"
                detail = plan.detail
                if plan.refusal == "not-execute":
                    kind = "wrong-phase"
                elif plan.refusal == "baseline-conflict":
                    kind = "already-started"
                    detail = (
                        f"{placement.path} records start_sha {item.start_sha}, but HEAD "
                        f"{tip.commit or '(predicted merge)'} at {placement.worktree} does not descend from it; "
                        "inspect where this stage's work started before recording"
                    )
                return replace(result, refusal=PlaceRefusal(kind, detail))
            if tip.commit is None and plan.changed:
                return replace(
                    result,
                    start_sha=(None, None),
                    warnings=(
                        "execute baseline SHA is unknown until the predicted refresh merge is performed; "
                        "run gw work place --start without --dry-run to record it",
                    ),
                )
            result = replace(result, start_sha=(plan.before, plan.after))
            if plan.changed and not ctx.dry_run:
                application = apply_mutation(
                    ctx.layout,
                    _baseline_mutation(context.bundle, plan),
                    repo_root=placement.repo_root,
                    repo_roots=resolve_repos(ctx.layout),
                    baseline_bundle=context.bundle,
                    commit=WorkspaceCommit(
                        f"workspace: record {item_stem(placement.path)} execute baseline", items=(placement.path,)
                    ),
                )
                result = replace(
                    result,
                    start_sha=(plan.before, plan.after if application.ok else plan.before),
                    application=application,
                    warnings=(*application.failures, *application.warnings),
                )
    except (OSError, ValueError) as exc:
        result = replace(
            result,
            refusal=PlaceRefusal("unknown-item" if isinstance(exc, ValueError) else "git-unavailable", str(exc)),
        )
    return result

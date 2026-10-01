"""`gw work prepare-workspace`: create and stamp gw-owned workspace worktrees (D-002).

Plans by default. For *path*, outermost-first over its enclosing Epic/Release
owners and then *path* itself, each owner's `_workspace` stamp is verified,
or an exact existing checkout is adopted, or one is created with
`git worktree add -b`. Each new stamp is recorded through
`run_record_placement(repo="_workspace")` under the decision-owner lock with a
preparation guard. Runs no Orca command. Idempotent: a replay after a crash
adopts what it created.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Literal

from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.pipeline import code_phases

from graph_works_core.orchestrate.anchors import workspace_chain, workspace_worktree_path
from graph_works_core.orchestrate.commands import branch_name
from graph_works_core.orchestrate.placement import preparation_guard, run_record_placement
from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.provenance import probe_git
from graph_works_core.workspace.repo_context import RepositoryContext, observe_repository
from graph_works_core.workspace.workspace_branch import WORKSPACE_REPO, workspace_repo

WorkspacePrepareRefusal = Literal[
    "unknown-path", "not-entitled", "workspace-unprovable", "workspace-ambiguous", "stamp-refused"
]
_ITEM_PHASES = code_phases()


@dataclass(frozen=True, slots=True)
class WorkspaceStep:
    owner_path: str
    owner_phase: str | None
    worktree: str
    branch: str
    base_branch: str
    action: Literal["verified", "adopt", "create"]
    recorded: bool = False


@dataclass(frozen=True, slots=True)
class WorkspacePrepareResult:
    path: str
    steps: tuple[WorkspaceStep, ...]
    refusal: WorkspacePrepareRefusal | None
    detail: str
    note: str | None
    applied: bool

    @property
    def placement(self) -> WorkspaceStep | None:
        if self.refusal is not None or not self.steps or self.steps[-1].owner_path != self.path:
            return None
        return self.steps[-1]


def _classify(
    owner: WorkItem, context: RepositoryContext, worktree: str, branch: str
) -> tuple[Literal["verified", "adopt", "create"] | None, WorkspacePrepareRefusal | None, str]:
    if "repo_stamps" in owner.invalid_optional_fields:
        return None, "workspace-unprovable", f"repair malformed repo_stamps on {owner.path}"
    if not context.inventory_known or not context.branches_known or not context.identity_known:
        return None, "workspace-unprovable", "repository inventory, branches or identity is unknown"
    stamp = owner.repo_stamps.get(WORKSPACE_REPO)
    if stamp is not None:
        if len(context.inventory.get(stamp.branch, ())) > 1:
            return None, "workspace-ambiguous", f"{stamp.branch!r} is checked out more than once"
        stamped = str(Path(stamp.worktree).resolve())
        if (
            context.inventory.get(stamp.branch) != (stamped,)
            or context.path_exists.get(stamped) is not True
            or context.checkout_usable_by_path.get(stamped) is not True
        ):
            return (
                None,
                "workspace-unprovable",
                f"{owner.path}: stamped workspace {stamped!r} on {stamp.branch!r} is not a clean verified checkout",
            )
        return "verified", None, ""
    matches = context.inventory.get(branch, ())
    if len(matches) > 1:
        return None, "workspace-ambiguous", f"{branch!r} is checked out more than once"
    if matches == (worktree,):
        if context.path_exists.get(worktree) is not True or context.checkout_usable_by_path.get(worktree) is not True:
            return None, "workspace-unprovable", f"{worktree!r} is dirty or unreadable"
        return "adopt", None, ""
    if matches:
        return None, "workspace-ambiguous", f"{branch!r} is checked out at {matches[0]!r}, not {worktree!r}"
    if branch in context.branches or not context.branches_known:
        return None, "workspace-unprovable", f"{branch!r} exists with no provable checkout"
    if Path(worktree).exists() or Path(worktree).is_symlink():
        return None, "workspace-ambiguous", f"{worktree!r} exists but is not {branch!r}"
    if context.path_exists.get(worktree) is not False:
        return None, "workspace-unprovable", f"{worktree!r} availability is unknown"
    return "create", None, ""


def _checkout_matches(context: RepositoryContext, worktree: str, branch: str) -> bool:
    """Inventory alone does not prove a registered directory still belongs to us."""
    common = probe_git(Path(worktree), "rev-parse", "--path-format=absolute", "--git-common-dir")
    head = probe_git(Path(worktree), "symbolic-ref", "--short", "HEAD")
    return (
        common.returncode == 0
        and bool(common.stdout.strip())
        and str(Path(common.stdout.strip()).resolve()) == context.identity
        and head.returncode == 0
        and head.stdout.strip() == branch
    )


def run_prepare_workspace(
    layout: WorkspaceLayout, path: str, *, today: date, apply: bool = False
) -> WorkspacePrepareResult:
    workspace, note = workspace_repo(layout)
    if workspace is None:
        return WorkspacePrepareResult(path, (), None, "", note, False)
    workspace_path = workspace.path
    assert workspace_path is not None
    bundle = load_workspace_bundle(layout, ignore=IGNORE)
    items = {i.path: i for i in load_items(bundle)}
    item = items.get(path)
    if item is None:
        return WorkspacePrepareResult(path, (), "unknown-path", f"unknown work item {path!r}", None, False)
    chain = workspace_chain(item, items)
    worktrees_dir = str(layout.worktrees_dir.resolve())

    def observe() -> RepositoryContext:
        planned = (workspace_worktree_path(worktrees_dir, o.path, o.type) for o in chain)
        stamped = (Path(o.repo_stamps[WORKSPACE_REPO].worktree) for o in chain if WORKSPACE_REPO in o.repo_stamps)
        return observe_repository(workspace_path, paths=(*map(Path, planned), *stamped))

    steps: list[WorkspaceStep] = []
    applied = False
    planned_chain = tuple(o.path for o in chain)
    for index, owner_path in enumerate(planned_chain):
        # Earlier steps legitimately stamped their pages. Re-read for this step,
        # then bind entitlement, parent selection and the guard to that bundle.
        bundle = load_workspace_bundle(layout, ignore=IGNORE)
        items = {i.path: i for i in load_items(bundle)}
        item = items.get(path)
        if item is None:
            return WorkspacePrepareResult(
                path, tuple(steps), "unknown-path", f"unknown work item {path!r}", None, applied
            )
        chain = workspace_chain(item, items)
        if tuple(o.path for o in chain) != planned_chain:
            return WorkspacePrepareResult(
                path, tuple(steps), "stamp-refused", "workspace owner chain changed; replan", None, applied
            )
        if chain[-1].phase not in _ITEM_PHASES or any(o.phase != "execute" for o in chain[:-1]):
            phases = ", ".join(f"{o.path}={o.phase}" for o in chain)
            return WorkspacePrepareResult(
                path,
                tuple(steps),
                "not-entitled",
                f"needs item at execute/finish and owners at execute: {phases}",
                None,
                applied,
            )
        for parent, previous in zip(chain[:index], steps, strict=True):
            stamp = parent.repo_stamps.get(WORKSPACE_REPO)
            if apply and (
                "repo_stamps" in parent.invalid_optional_fields
                or stamp is None
                or stamp.branch != previous.branch
                or str(Path(stamp.worktree).resolve()) != previous.worktree
            ):
                return WorkspacePrepareResult(
                    path,
                    tuple(steps),
                    "stamp-refused",
                    f"{parent.path}: workspace parent placement changed; replan",
                    None,
                    applied,
                )
        owner = items[owner_path]
        context = observe()
        base = steps[-1].branch if steps else context.default_base
        document = bundle.concepts[owner.path]
        if "repo_stamps" in document.fm_raw and document.fm_raw["repo_stamps"] is None:
            return WorkspacePrepareResult(
                path,
                tuple(steps),
                "workspace-unprovable",
                f"repair malformed repo_stamps on {owner.path}",
                None,
                applied,
            )
        stamp = owner.repo_stamps.get(WORKSPACE_REPO)
        worktree = (
            str(Path(stamp.worktree).resolve())
            if stamp
            else workspace_worktree_path(worktrees_dir, owner.path, owner.type)
        )
        branch = stamp.branch if stamp else branch_name(owner.path, owner.type)
        action, refusal, detail = _classify(owner, context, worktree, branch)
        if refusal is not None:
            return WorkspacePrepareResult(path, tuple(steps), refusal, f"{owner.path}: {detail}", None, applied)
        assert action is not None
        step = WorkspaceStep(owner.path, owner.phase, worktree, branch, base, action)
        if action != "create" and not _checkout_matches(context, worktree, branch):
            return WorkspacePrepareResult(
                path,
                tuple(steps),
                "workspace-unprovable",
                f"{owner.path}: checkout identity or branch changed",
                None,
                applied,
            )
        if action == "verified":
            # A previous stamp write may have survived a failed commit. Never
            # turn that pending page into committed success on replay.
            member = (layout.bundle_dir / owner.page_path).relative_to(workspace_path).as_posix()
            tracked = probe_git(workspace_path, "ls-files", "--error-unmatch", "--", member)
            committed = probe_git(workspace_path, "diff", "--quiet", "HEAD", "--", member)
            if tracked.returncode != 0 or committed.returncode != 0:
                return WorkspacePrepareResult(
                    path,
                    tuple(steps),
                    "stamp-refused",
                    f"{owner.path}: stamped page is not committed; repair the pending workspace commit",
                    None,
                    applied,
                )
        preview = run_record_placement(
            layout,
            owner.path,
            root=owner.path,
            phase=owner.phase or "",
            worktree=worktree,
            branch=branch,
            today=today,
            repo=WORKSPACE_REPO,
        )
        if preview.plan.refusal is not None:
            return WorkspacePrepareResult(path, tuple(steps), "stamp-refused", preview.plan.detail, None, applied)
        if apply and action != "verified":
            guard = preparation_guard(layout, owner.path, bundle=bundle)
            if action == "create":
                parent_existed = Path(worktree).parent.exists()
                added = probe_git(workspace_path, "worktree", "add", "-b", branch, worktree, base)
                if added.returncode != 0:
                    after = observe()
                    applied = (
                        applied
                        or branch in after.branches
                        or Path(worktree).exists()
                        or (not parent_existed and Path(worktree).parent.exists())
                    )
                    return WorkspacePrepareResult(
                        path,
                        tuple(steps),
                        "workspace-unprovable",
                        f"{owner.path}: git worktree add failed: {added.stderr.strip()}",
                        None,
                        applied,
                    )
                applied = True
                context = observe()
                _, refusal, detail = _classify(owner, context, worktree, branch)
                if refusal is not None or not _checkout_matches(context, worktree, branch):
                    return WorkspacePrepareResult(
                        path,
                        tuple(steps),
                        refusal or "workspace-unprovable",
                        detail or "created checkout identity or branch changed",
                        None,
                        applied,
                    )
            try:
                record = run_record_placement(
                    layout,
                    owner.path,
                    root=owner.path,
                    phase=owner.phase or "",
                    worktree=worktree,
                    branch=branch,
                    today=today,
                    repo=WORKSPACE_REPO,
                    dry_run=False,
                    expected_preparation=guard,
                )
            except WorkspaceError as exc:
                return WorkspacePrepareResult(
                    path, tuple(steps), "stamp-refused", f"{owner.path}: {exc}", None, applied
                )
            applied = applied or record.written
            if record.plan.refusal is not None or (record.plan.changed and not record.written):
                reason = record.plan.detail or str(record.application)
                return WorkspacePrepareResult(
                    path, tuple(steps), "stamp-refused", f"{owner.path}: {reason}", None, applied
                )
            outcome = record.application.commit if record.application else record.pending_commit
            if (
                outcome is None
                or outcome.status == "failed"
                or (outcome.status == "skipped" and outcome.reason != "no-changes")
            ):
                reason = "; ".join(record.warnings) or (
                    (outcome.reason or outcome.status) if outcome else "missing commit outcome"
                )
                return WorkspacePrepareResult(
                    path, tuple(steps), "stamp-refused", f"{owner.path}: {reason}", None, applied
                )
            step = replace(step, recorded=True)
            context = observe()
        steps.append(step)
    return WorkspacePrepareResult(path, tuple(steps), None, "", None, applied)


__all__ = ["WorkspacePrepareRefusal", "WorkspacePrepareResult", "WorkspaceStep", "run_prepare_workspace"]

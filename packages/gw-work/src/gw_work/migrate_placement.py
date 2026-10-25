"""`gw work migrate-placement`: clean up what retired placement stamps point at, then strip them.

Plan by default: `plan_migrate_placement` writes nothing and `apply_migrate_placement` applies. The plan reads
**raw frontmatter** (`fm_raw`), never the `WorkItem` placement model, so it keeps working after that model's
legacy field is deleted. Every item is collected, active and archived.

A legacy `repo_stamps._workspace` entry (the retired workspace-branch placement) is stripped like any other
retired stamp, matching `state.retired-placement`. Its worktree and branch are never removed: the plan and the
result report each one (path, branch, commits ahead of the workspace `main` when git can tell) so a human can
merge or prune it by hand.
"""

from __future__ import annotations

import fnmatch
import hashlib
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from gw_kernel.workspace.bundle import load_work_bundle
from gw_kernel.workspace.commits import CommitOutcome, WorkspaceCommit
from gw_kernel.workspace.errors import WorkspaceError
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.provenance import GitExecutable, GitFailure, gate_git, strict_git, strict_is_ancestor
from gw_kernel.workspace.repos import declared_repositories, resolve_item_repo, resolve_repos
from gw_pipeline.finish import read_finish_receipt
from gw_pipeline.placement import active_path, branch_name
from gw_pipeline.repo_context import RepositoryContext, observe_branch_tips, observe_repository
from gw_pipeline.transactions import apply_mutation
from okf_ext.locking import locked
from okf_io import append_log_entry, load
from work_tracker_okf.items import WorkItem, load_items
from work_tracker_okf.mutation import PlannedWrite, WorkMutationPlan
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

COMMIT_SUBJECT = "workspace: migrate-placement strip retired placement fields"
REMOVE_TIMEOUT_SECONDS = 120.0
ORPHAN_BRANCH_GLOB = "psprowls/gw-reader-*"
_WORKSPACE_KEY = "_workspace"

Action = Literal["strip", "remove-worktree", "delete-branch", "keep"]


@dataclass(frozen=True, slots=True)
class MigrationRow:
    item: str
    repo: str
    worktree: str | None
    branch: str | None
    action: Action
    reason: str | None


@dataclass(frozen=True, slots=True)
class MigrationBlocker:
    item: str
    repo: str
    worktree: str
    legacy_branch: str
    computed_branch: str
    commands: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MigrationOrphan:
    repo: str
    branch: str


@dataclass(frozen=True, slots=True)
class WorkspaceStamp:
    """A stripped legacy `_workspace` stamp: left on disk for a human to merge or prune."""

    item: str
    worktree: str | None
    branch: str | None
    ahead: int | None


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    rows: tuple[MigrationRow, ...]
    blockers: tuple[MigrationBlocker, ...]
    orphans: tuple[MigrationOrphan, ...]
    strips: tuple[str, ...]
    refusal: str | None
    workspace_stamps: tuple[WorkspaceStamp, ...] = ()

    @property
    def nothing_to_migrate(self) -> bool:
        return not self.strips and not self.blockers and self.refusal is None


@dataclass(frozen=True, slots=True)
class MigrationResult:
    removed_worktrees: tuple[str, ...] = ()
    deleted_branches: tuple[str, ...] = ()
    kept: tuple[MigrationRow, ...] = ()
    stripped: tuple[str, ...] = ()
    commit: CommitOutcome | None = None
    refusal: str | None = None
    failed_step: str | None = None
    workspace_stamps: tuple[WorkspaceStamp, ...] = ()


@dataclass(slots=True)
class _Group:
    repo: str
    repo_path: Path | None
    worktree: str
    branch: str
    items: list[WorkItem] = field(default_factory=list)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _canonical(path: str, base: Path) -> str:
    candidate = Path(path)
    return str((candidate if candidate.is_absolute() else base / candidate).resolve())


def _is_terminal(item: WorkItem) -> bool:
    return item.work_status in TERMINAL_STATUSES


def _spec_baseline_has_workspace(raw: Mapping[Any, Any]) -> bool:
    baseline = raw.get("spec_baseline")
    return isinstance(baseline, Mapping) and "workspace" in baseline


def _retired_stamp_keys(value: object) -> list[str]:
    """The `repo_stamps` keys migration strips: every one, the legacy `_workspace` entry included."""
    return [str(key) for key in value] if isinstance(value, Mapping) else []


def _needs_strip(raw: Mapping[Any, Any]) -> bool:
    stamps = raw.get("repo_stamps")
    return (
        "worktree" in raw
        or "branch" in raw
        or bool(_retired_stamp_keys(stamps))
        or ("repo_stamps" in raw and not isinstance(stamps, Mapping))
        or _spec_baseline_has_workspace(raw)
    )


def _workspace_stamp(
    layout: WorkspaceLayout, item: WorkItem, raw: Mapping[Any, Any], git: GitExecutable | None
) -> WorkspaceStamp | None:
    stamps = raw.get("repo_stamps")
    if not isinstance(stamps, Mapping) or _WORKSPACE_KEY not in stamps:
        return None
    entry = stamps[_WORKSPACE_KEY]
    fields = entry if isinstance(entry, Mapping) else {}
    worktree, branch = _text(fields.get("worktree")), _text(fields.get("branch"))
    ahead: int | None = None
    if branch is not None and git is not None:
        out = strict_git(layout.root, "rev-list", "--count", f"main..{branch}", "--", git=git)
        if not isinstance(out, GitFailure) and out.strip().isdigit():
            ahead = int(out.strip())
    return WorkspaceStamp(item.path, worktree, branch, ahead)


def _stamp_rows(
    layout: WorkspaceLayout,
    item: WorkItem,
    raw: Mapping[Any, Any],
    items: Mapping[str, WorkItem],
    declared: Mapping[str, Path],
) -> list[tuple[str, Path | None, str | None, str | None]]:
    """`(repo, repo_path, worktree, branch)` for each placement the item's raw frontmatter stores."""
    found: list[tuple[str, Path | None, str | None, str | None]] = []
    if "worktree" in raw or "branch" in raw:
        try:
            own = resolve_item_repo(layout, item, items)
            name, path = own.name or "", own.path
        except WorkspaceError:
            name, path = "", None
        found.append((name, path, _text(raw.get("worktree")), _text(raw.get("branch"))))
    stamps = raw.get("repo_stamps")
    if isinstance(stamps, Mapping):
        for key, value in stamps.items():
            if key == _WORKSPACE_KEY:
                continue  # reported as a WorkspaceStamp, never cleaned up
            fields = value if isinstance(value, Mapping) else {}
            found.append((str(key), declared.get(str(key)), _text(fields.get("worktree")), _text(fields.get("branch"))))
    return found


def _receipt_entries(layout: WorkspaceLayout, group: _Group) -> list[Any]:
    entries: list[Any] = []
    for item in group.items:
        for candidate in dict.fromkeys((item.path, active_path(item.path))):
            _, found, error = read_finish_receipt(layout, candidate)
            if error is None and found:
                entries.extend(entry for entry in found if entry.repo == group.repo)
                break
    return entries


def _classify_branch(
    layout: WorkspaceLayout,
    group: _Group,
    observed: RepositoryContext,
    git: GitExecutable,
    removed: set[str],
) -> tuple[Action, str | None]:
    branch = group.branch
    tip = observed.branch_tips.get(branch)
    if tip is None:
        return "strip", "absent"
    if any(not _is_terminal(item) for item in group.items):
        return "keep", "open"
    if branch == observed.default_base:
        return "keep", "default-branch"
    holders = [path for path in observed.inventory.get(branch, ()) if path not in removed]
    if holders:
        return "keep", "occupied"
    entries = _receipt_entries(layout, group)
    if any(entry.source_commit == tip and entry.evidence in {"verified", "accepted"} for entry in entries):
        return "delete-branch", "receipt"
    targets = dict.fromkeys((*(entry.target_branch for entry in entries), observed.default_base))
    assert group.repo_path is not None
    for target in targets:
        if target == branch or target not in observed.branch_tips:
            continue
        answer = strict_is_ancestor(group.repo_path, tip, target, git=git)
        if answer is True:
            return "delete-branch", f"ancestor-of-{target}"
    return "keep", "unmerged"


def _classify(
    layout: WorkspaceLayout,
    group: _Group,
    observed: RepositoryContext,
    git: GitExecutable,
    users: Mapping[tuple[str, str], list[WorkItem]],
) -> tuple[list[tuple[Action, str | None, bool]], list[MigrationBlocker]]:
    """The steps for one `(repo, worktree, branch)`: `(action, reason, is_worktree_step)` plus blockers."""
    blockers: list[MigrationBlocker] = []
    steps: list[tuple[Action, str | None, bool]] = []
    removed: set[str] = set()
    canonical = _canonical(group.worktree, layout.root) if group.worktree else ""
    if group.worktree:
        assert group.repo_path is not None
        if canonical in {str(group.repo_path.resolve()), str(layout.root.resolve())}:
            return [("strip", "declared-checkout", True)], blockers
        present = observed.path_exists.get(canonical) is True
        if present:
            open_here = [item for item in group.items if not _is_terminal(item)]
            if open_here:
                for item in open_here:
                    computed = branch_name(active_path(item.path), item.type)
                    if group.branch != computed:
                        blockers.append(
                            MigrationBlocker(
                                item.path,
                                group.repo,
                                group.worktree,
                                group.branch,
                                computed,
                                (
                                    f"git -C {group.worktree} branch -m {group.branch} {computed}",
                                    f"gw work place {item.path}",
                                ),
                            )
                        )
                reason = "in-flight" if blockers else "adoptable"
                return [("keep", reason, True)], blockers
            if any(not _is_terminal(item) for item in users.get((group.repo, canonical), ())):
                return [("keep", "in-use", True)], blockers
            if observed.checkout_usable_by_path.get(canonical) is not True:
                return [("keep", "dirty", True)], blockers
            steps.append(("remove-worktree", None, True))
            removed.add(canonical)
    if group.branch:
        action, branch_reason = _classify_branch(layout, group, observed, git, removed)
        if not steps or action != "strip":
            steps.append((action, branch_reason, False))
    if not steps:
        steps.append(("strip", None, True))
    return steps, blockers


def plan_migrate_placement(layout: WorkspaceLayout) -> MigrationPlan:
    """Classify every stored legacy placement. Writes nothing."""
    bundle = load_work_bundle(layout)
    items = load_items(bundle).by_path
    try:
        declared = declared_repositories(layout)
    except WorkspaceError as exc:
        return MigrationPlan((), (), (), (), f"workspace: {exc}")
    strips: list[str] = []
    workspace_raw: list[tuple[WorkItem, Mapping[Any, Any]]] = []
    groups: dict[tuple[str, str, str], _Group] = {}
    unresolved: list[MigrationRow] = []
    for path in sorted(items):
        item, document = items[path], bundle.concepts.get(path)
        if document is None:
            continue
        raw = document.fm_raw
        if not _needs_strip(raw):
            continue
        strips.append(path)
        if _WORKSPACE_KEY in (raw.get("repo_stamps") if isinstance(raw.get("repo_stamps"), Mapping) else {}):
            workspace_raw.append((item, raw))
        for repo, repo_path, worktree, branch in _stamp_rows(layout, item, raw, items, declared):
            if not worktree and not branch:
                continue
            if repo_path is None:
                unresolved.append(MigrationRow(path, repo, worktree, branch, "strip", "no-repository"))
                continue
            key = (repo, worktree or "", branch or "")
            groups.setdefault(key, _Group(repo, repo_path, worktree or "", branch or "")).items.append(item)

    users: dict[tuple[str, str], list[WorkItem]] = {}
    for group in groups.values():
        if group.worktree:
            users.setdefault((group.repo, _canonical(group.worktree, layout.root)), []).extend(group.items)

    rows: list[MigrationRow] = list(unresolved)
    blockers: list[MigrationBlocker] = []
    observed_by_repo: dict[str, RepositoryContext] = {}
    git: GitExecutable | GitFailure | None = None
    if groups:
        git = gate_git(layout)
        if isinstance(git, GitFailure):
            return MigrationPlan((), (), (), tuple(strips), f"git-unavailable: {git.detail}")
        for repo in sorted({group.repo for group in groups.values()}):
            repo_groups = [group for group in groups.values() if group.repo == repo]
            repo_path = repo_groups[0].repo_path
            assert repo_path is not None
            observed = observe_repository(
                repo_path,
                paths=tuple(Path(_canonical(group.worktree, layout.root)) for group in repo_groups if group.worktree),
                git=git,
            )
            if not (observed.identity_known and observed.inventory_known and observed.branch_tips_known):
                return MigrationPlan((), (), (), tuple(strips), f"git-unavailable: cannot observe repository {repo}")
            observed_by_repo[repo] = observed
        for key in sorted(groups):
            group = groups[key]
            steps, found = _classify(layout, group, observed_by_repo[group.repo], git, users)
            blockers.extend(found)
            for item in group.items:
                for action, reason, _is_worktree in steps:
                    rows.append(
                        MigrationRow(
                            item.path,
                            group.repo,
                            group.worktree or None,
                            group.branch or None,
                            action,
                            reason,
                        )
                    )
    named = {(row.repo, row.branch) for row in rows if row.branch}
    orphans: list[MigrationOrphan] = []
    if isinstance(git, GitExecutable):
        for repo, repo_path in sorted(declared.items()):
            tips = (
                observed_by_repo[repo].branch_tips
                if repo in observed_by_repo
                else observe_branch_tips(repo_path, git=git)
            )
            orphans.extend(
                MigrationOrphan(repo, branch)
                for branch in sorted(tips or {})
                if fnmatch.fnmatchcase(branch, ORPHAN_BRANCH_GLOB) and (repo, branch) not in named
            )
    stamp_git = git if isinstance(git, GitExecutable) else (gate_git(layout) if workspace_raw else None)
    workspace_stamps = tuple(
        stamp
        for item, raw in workspace_raw
        if (stamp := _workspace_stamp(layout, item, raw, stamp_git if isinstance(stamp_git, GitExecutable) else None))
    )
    return MigrationPlan(
        tuple(rows),
        tuple(dict.fromkeys(blockers)),
        tuple(orphans),
        tuple(strips),
        None,
        workspace_stamps,
    )


def _strip_document(document: Any) -> None:  # noqa: ANN401 -- okf_io.Document, kept untyped to avoid a re-export
    raw = document.fm_raw
    changed = False
    for key in ("worktree", "branch"):
        if key in raw:
            del raw[key]
            changed = True
    if "repo_stamps" in raw:
        stamps = raw["repo_stamps"]
        if isinstance(stamps, MutableMapping):
            for key in _retired_stamp_keys(stamps):
                del stamps[key]
                changed = True
            if not stamps:
                del raw["repo_stamps"]
        else:
            del raw["repo_stamps"]
            changed = True
    if _spec_baseline_has_workspace(raw):
        baseline: MutableMapping[Any, Any] = raw["spec_baseline"]
        del baseline["workspace"]
        if not baseline:
            del raw["spec_baseline"]
        changed = True
    if changed:
        document.mark_dirty()


def _log_text(plan: MigrationPlan, stripped: int) -> str:
    kept = dict.fromkeys(
        f"{row.repo}:{row.worktree or row.branch} ({row.reason})" for row in plan.rows if row.action == "keep"
    )
    orphans = [f"{orphan.repo}:{orphan.branch}" for orphan in plan.orphans]
    return (
        f"**note** migrate-placement — stripped retired placement fields from {stripped} items; "
        f"kept: {'; '.join(kept) or 'none'}; orphans: {'; '.join(orphans) or 'none'}"
    )


def apply_migrate_placement(layout: WorkspaceLayout, plan: MigrationPlan, *, today: date) -> MigrationResult:
    """Clean up git, then strip every retired key in one workspace commit."""
    kept = tuple(row for row in plan.rows if row.action == "keep")
    if plan.refusal is not None:
        return MigrationResult(kept=kept, refusal=plan.refusal)
    if plan.blockers:
        return MigrationResult(kept=kept, refusal="legacy-in-flight")
    if plan.nothing_to_migrate:
        return MigrationResult(kept=kept)

    removals = tuple(dict.fromkeys((row.repo, row.worktree) for row in plan.rows if row.action == "remove-worktree"))
    deletions = tuple(dict.fromkeys((row.repo, row.branch) for row in plan.rows if row.action == "delete-branch"))
    removed: list[str] = []
    deleted: list[str] = []
    if removals or deletions:
        git = gate_git(layout)
        if isinstance(git, GitFailure):
            return MigrationResult(kept=kept, refusal="git-unavailable", failed_step=git.detail)
        declared = declared_repositories(layout)
        for repo in sorted({repo for repo, _ in (*removals, *deletions)}):
            repo_path = declared.get(repo)
            if repo_path is None:
                return MigrationResult(kept=kept, refusal="git-unavailable", failed_step=f"unknown repository {repo}")
            with locked(layout.cache_dir / "placement" / f"{repo}.lock"):
                steps = [
                    *(
                        (("worktree", "remove", worktree), worktree)
                        for r, worktree in removals
                        if r == repo and worktree
                    ),
                    *((("branch", "-D", branch), branch) for r, branch in deletions if r == repo and branch),
                ]
                for args, subject in steps:
                    outcome = strict_git(repo_path, *args, git=git, timeout=REMOVE_TIMEOUT_SECONDS)
                    if isinstance(outcome, GitFailure):
                        return MigrationResult(
                            tuple(removed),
                            tuple(deleted),
                            kept,
                            (),
                            None,
                            "git-failed",
                            f"git {' '.join(args)}: {outcome.detail}",
                        )
                    (removed if args[0] == "worktree" else deleted).append(subject)

    bundle = load_work_bundle(layout)
    writes: list[PlannedWrite] = []
    for path in plan.strips:
        document = bundle.concepts[path]
        before = document.serialize().encode("utf-8")
        _strip_document(document)
        writes.append(
            PlannedWrite(f"{path}.md", hashlib.sha256(before).hexdigest(), document.serialize().encode("utf-8"))
        )
    log = load(layout.bundle_dir / "log.md")
    appended = append_log_entry(log, _log_text(plan, len(plan.strips)), on=today, dry_run=True)
    writes.append(
        PlannedWrite(
            "log.md", hashlib.sha256(appended.before.encode("utf-8")).hexdigest(), appended.after.encode("utf-8")
        )
    )
    mutation = WorkMutationPlan(
        root=bundle.root,
        operation="migrate",
        path_mapping={},
        move_plan=None,
        moves=(),
        writes=tuple(sorted(writes, key=lambda write: write.member)),
        deletes=(),
        mkdirs=(),
        warnings=(),
        refusals=(),
        validate_paths=(),
    )
    applied = apply_mutation(layout, mutation, repo_roots=resolve_repos(layout), commit=WorkspaceCommit(COMMIT_SUBJECT))
    if not applied.ok:
        return MigrationResult(
            tuple(removed), tuple(deleted), kept, (), applied.commit, "apply-failed", "; ".join(applied.failures)
        )
    return MigrationResult(
        tuple(removed), tuple(deleted), kept, plan.strips, applied.commit, workspace_stamps=plan.workspace_stamps
    )


__all__ = [
    "COMMIT_SUBJECT",
    "MigrationBlocker",
    "MigrationOrphan",
    "MigrationPlan",
    "MigrationResult",
    "MigrationRow",
    "WorkspaceStamp",
    "apply_migrate_placement",
    "plan_migrate_placement",
]

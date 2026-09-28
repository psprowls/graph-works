"""Complete repository-local finish targets shared by attended and relay routing."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from okf_io import Bundle, Document, load_bundle, parse
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.provenance import probe_git
from graph_works_core.workspace.repo_context import RepositoryContext, observe_repository
from graph_works_core.workspace.repos import ItemRepo, declared_repositories, resolve_item_repo
from graph_works_core.workspace.workspace_branch import WORKSPACE_REPO, workspace_repo


@dataclass(frozen=True, slots=True)
class FinishTarget:
    repo: ItemRepo
    worktree: str  # source checkout the finish worker runs in
    source_branch: str
    target_branch: str
    target_worktree: str | None  # unique checkout holding target_branch, when one exists


@dataclass(frozen=True, slots=True)
class FinishOccupancy:
    worktrees: tuple[str, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class FinishPlan:
    targets: tuple[FinishTarget, ...]
    blockers: tuple[str, ...]
    # Target evidence survives failed admission; None is legacy/absent evidence.
    occupancy: FinishOccupancy | None = None


def enclosing_owner(item: WorkItem, items: Mapping[str, WorkItem]) -> WorkItem | None:
    parent = item.parent_path
    seen: set[str] = set()
    while parent and parent not in seen:
        seen.add(parent)
        owner = items.get(parent)
        if owner is None:
            return None
        if owner.type in {"Epic", "Release"} or (owner.type == "Feature" and owner.child_paths):
            return owner
        parent = owner.parent_path
    return None


def resolve_finish_targets(
    layout: WorkspaceLayout,
    items: Sequence[WorkItem],
    path: str,
    *,
    single_repo: ItemRepo | None = None,
    repo_contexts: Mapping[str, RepositoryContext] = MappingProxyType({}),
) -> FinishPlan:
    """Read and validate all owned stamps; no placement or Git mutation.

    `single_repo` preserves the explicit Python repository override without
    assigning any foreign stamps. The orchestration shell may supply its
    already-observed `repo_contexts` to avoid probing those repositories twice.
    """
    by_path = {item.path: item for item in items}
    item = by_path[path]
    targets: list[FinishTarget] = []
    blockers: list[str] = []
    occupied: set[str] = set()
    occupancy_known = "repo_stamps" not in item.invalid_optional_fields
    if "repo_stamps" in item.invalid_optional_fields:
        blockers.append(f"{path}: repair malformed repo_stamps before finishing")
    code_stamps = {name: stamp for name, stamp in item.repo_stamps.items() if name != WORKSPACE_REPO}
    ws_stamp = item.repo_stamps.get(WORKSPACE_REPO)
    if single_repo is not None and code_stamps:
        return FinishPlan((), (f"{path}: explicit repo override cannot finish foreign repo_stamps",))
    observed = dict(repo_contexts)

    def context_for(repo: ItemRepo) -> RepositoryContext:
        assert repo.path is not None
        context = next((c for c in observed.values() if str(repo.path) in c.checkout_usable_by_path), None)
        if context is None:
            context = observe_repository(repo.path)
            observed[context.identity] = context
        return context

    candidates: list[tuple[ItemRepo, str | None, str | None]] = []
    try:
        declared = declared_repositories(layout)
        outer = enclosing_owner(item, by_path)
        outer_repo = (single_repo or resolve_item_repo(layout, outer, by_path)) if outer is not None else None
        if item.branch or item.worktree:
            candidates.append((single_repo or resolve_item_repo(layout, item, by_path), item.worktree, item.branch))
        elif not code_stamps and ws_stamp is None and item.type not in {"Epic", "Release"}:
            own_repo = single_repo or resolve_item_repo(layout, item, by_path)
            if outer is not None:
                # A descendant's source is the enclosing anchor it finishes
                # into, not the declared checkout; the declared checkout may
                # sit on an unrelated branch (e.g. trunk) with no ancestry
                # relationship to the anchor.
                outer_stamp = outer.repo_stamps.get(own_repo.name) if own_repo.name is not None else None
                own = outer_repo is not None and outer_repo.name == own_repo.name
                anchor_worktree, anchor_branch = (
                    (outer.worktree, outer.branch)
                    if own
                    else ((outer_stamp.worktree, outer_stamp.branch) if outer_stamp else (None, None))
                )
                candidates.append((own_repo, anchor_worktree, anchor_branch))
            elif own_repo.path is None:
                # Main-mode leaves may finish without owning a dedicated
                # branch. The declared checkout is the compatibility
                # location, but its current branch must be observed rather
                # than inferred from trunk.
                occupancy_known = False
                blockers.append(f"{path}: no repository checkout can be verified for unstamped finish")
            else:
                checkout = str(own_repo.path.resolve())
                context = context_for(own_repo)
                branches = [branch for branch, paths in context.inventory.items() if checkout in paths]
                if len(branches) != 1:
                    occupancy_known = False
                    blockers.append(f"{path}: cannot verify current branch of unstamped finish checkout {checkout!r}")
                else:
                    candidates.append((own_repo, checkout, branches[0]))
        for name, stamp in sorted(code_stamps.items()):
            if name not in declared:
                occupancy_known = False
                blockers.append(f"{path}: stamped repo {name!r} is not declared")
            else:
                candidates.append((ItemRepo(name, declared[name], "frontmatter"), stamp.worktree, stamp.branch))
        if ws_stamp is not None:
            ws_repo, ws_note = workspace_repo(layout)
            if ws_repo is None:
                blockers.append(f"{path}: workspace branch is stamped but workspace placement is disabled ({ws_note})")
            else:
                candidates.append((ws_repo, ws_stamp.worktree, ws_stamp.branch))
    except WorkspaceError as exc:
        return FinishPlan((), (str(exc),))
    for repo, worktree, branch in candidates:
        if repo.path is None:
            occupancy_known = False
            blockers.append(f"{path}: repair incomplete finish stamp for {repo.name!r}")
            continue
        context = context_for(repo)
        target: str | None = context.default_base
        if outer is not None:
            outer_stamp = outer.repo_stamps.get(repo.name) if repo.name is not None else None
            own = outer_repo is not None and outer_repo.name == repo.name
            anchor_path, target = (
                (outer.worktree, outer.branch)
                if own
                else ((outer_stamp.worktree, outer_stamp.branch) if outer_stamp else (None, None))
            )
            if not anchor_path or not target:
                occupancy_known = False
                blockers.append(
                    f"{path}: prepare enclosing integration anchor {outer.path} in {repo.name!r} before finish"
                )
                continue
            anchor_path = str(Path(anchor_path).resolve())
            if context.identity_known and context.inventory_known and context.inventory.get(target) == (anchor_path,):
                occupied.add(anchor_path)
            else:
                occupancy_known = False
            if (
                "repo_stamps" in outer.invalid_optional_fields
                or context.inventory.get(target) != (anchor_path,)
                or context.path_exists.get(anchor_path) is not True
                or context.checkout_usable_by_path.get(anchor_path) is not True
            ):
                blockers.append(f"{path}: repair enclosing integration anchor {outer.path} in {repo.name!r}")
                continue
        if context.identity_known and context.inventory_known and target and context.inventory.get(target):
            occupied.update(context.inventory.get(target, ()))
        else:
            occupancy_known = False
        if not target or not context.branches_known or target not in context.branches:
            blockers.append(f"{path}: cannot verify target branch {target!r} in {repo.name!r}")
            continue
        holders = context.inventory.get(target, ())
        if len(holders) > 1:
            blockers.append(f"{path}: target branch {target!r} is checked out in several worktrees in {repo.name!r}")
            continue
        if not worktree or not branch:
            blockers.append(f"{path}: repair incomplete finish stamp for {repo.name!r}")
            continue
        worktree = str(Path(worktree).resolve())
        if (
            not context.identity_known
            or not context.inventory_known
            or context.inventory.get(branch) != (worktree,)
            or context.path_exists.get(worktree) is not True
            or context.checkout_usable_by_path.get(worktree) is not True
        ):
            blockers.append(f"{path}: cannot verify clean worktree {worktree!r} on branch {branch!r} in {repo.name!r}")
            continue
        target_worktree = holders[0] if holders else None
        targets.append(FinishTarget(repo, worktree, branch, target, target_worktree))
    return FinishPlan(
        tuple(targets), tuple(blockers), FinishOccupancy(tuple(sorted(occupied)), occupancy_known and bool(candidates))
    )


@dataclass(frozen=True, slots=True)
class VerifiedIntegration:
    repo: str
    source_branch: str
    source_commit: str
    target_branch: str
    result_commit: str


@dataclass(frozen=True, slots=True)
class FinishVerification:
    complete: bool
    resolved_in: str | None
    blockers: tuple[str, ...]
    entries: tuple[VerifiedIntegration, ...]


def read_finish_receipt(
    layout: WorkspaceLayout, path: str
) -> tuple[Document | None, tuple[VerifiedIntegration, ...], str | None]:
    """Parse strictly; historical entries are not yet verified Git evidence."""
    ref = artifact_ref(path, MANAGED_ARTIFACTS["finish-receipt"])
    try:
        raw = ref.path(layout.bundle_dir).read_bytes()
    except FileNotFoundError:
        return None, (), None
    except OSError as exc:
        return None, (), f"cannot read finish receipt: {exc}"
    try:
        doc = parse(raw.decode("utf-8"), path=ref.path(layout.bundle_dir))
        data = doc.fm_data()
        values = data.get("integrations")
        if (
            doc.parse_error
            or data.get("type") != "Explanation"
            or type(data.get("receipt_version")) is not int
            or data.get("receipt_version") != 1
            or data.get("owner") != path
            or not isinstance(values, list)
        ):
            return None, (), "malformed finish receipt"
        entries: list[VerifiedIntegration] = []
        for value in values:
            keys = ("repo", "source_branch", "source_commit", "target_branch", "result_commit")
            if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k] for k in keys):
                return None, (), "malformed finish receipt integration"
            entry = VerifiedIntegration(*(value[k] for k in keys))
            if any(e.repo == entry.repo for e in entries):
                return None, (), "duplicate finish receipt repository"
            if not all(
                re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha) for sha in (entry.source_commit, entry.result_commit)
            ):
                return None, (), "malformed finish receipt commit"
            entries.append(entry)
        return doc, tuple(entries), None
    except (UnicodeError, ValueError):
        return None, (), "malformed finish receipt"


def _commit(repo: Path, ref: str) -> str | None:
    result = probe_git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha) else None


def historical_integration(target: FinishTarget, entry: VerifiedIntegration) -> bool:
    """Historical evidence may be stale, but must remain genuine repository-local ancestry."""
    repo = target.repo.path
    return bool(
        repo is not None
        and entry.repo == target.repo.name
        and entry.source_branch == target.source_branch
        and entry.target_branch == target.target_branch
        and _commit(repo, entry.source_commit) == entry.source_commit
        and _commit(repo, entry.result_commit) == entry.result_commit
        and probe_git(repo, "merge-base", "--is-ancestor", entry.source_commit, entry.result_commit).returncode == 0
    )


def observe_integration(target: FinishTarget, entry: VerifiedIntegration | None = None) -> VerifiedIntegration | None:
    """Prove current source ancestry in this target's repository; never mutate Git."""
    repo = target.repo.path
    if repo is None or target.repo.name is None:
        return None
    source = _commit(repo, "refs/heads/" + target.source_branch)
    tip = _commit(repo, "refs/heads/" + target.target_branch)
    if source is None or tip is None:
        return None
    if entry is None:
        entry = VerifiedIntegration(target.repo.name, target.source_branch, source, target.target_branch, tip)
    if (
        entry.repo != target.repo.name
        or entry.source_branch != target.source_branch
        or entry.target_branch != target.target_branch
        or entry.source_commit != source
        or _commit(repo, entry.source_commit) != entry.source_commit
        or _commit(repo, entry.result_commit) != entry.result_commit
    ):
        return None
    for left, right in ((source, entry.result_commit), (entry.result_commit, tip)):
        if probe_git(repo, "merge-base", "--is-ancestor", left, right).returncode != 0:
            return None
    return entry


def inspect_finish(layout: WorkspaceLayout, path: str) -> FinishVerification:
    """Reverify every recorded integration against live repository-local refs."""
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    by_path = {item.path: item for item in items}
    if path not in by_path:
        return FinishVerification(False, None, (f"{path}: unknown finish owner",), ())
    plan = resolve_finish_targets(layout, items, path)
    _doc, entries, error = read_finish_receipt(layout, path)
    blockers = list(plan.blockers)
    if error:
        blockers.append(error)
    verified: list[VerifiedIntegration] = []
    for target in plan.targets:
        entry = next((e for e in entries if e.repo == target.repo.name), None)
        if entry is None or observe_integration(target, entry) is None:
            blockers.append(f"{target.repo.name}: incomplete integration into {target.target_branch}")
        else:
            verified.append(entry)
    names = {target.repo.name for target in plan.targets}
    if any(e.repo not in names for e in entries):
        blockers.append("finish receipt contains an unexpected repository")
    if not plan.targets:
        blockers.append("no verified finish targets")
    code_entries = [entry for entry in verified if entry.repo != WORKSPACE_REPO]
    resolved = (code_entries or verified)[0].result_commit if verified else None
    return FinishVerification(not blockers, resolved, tuple(blockers), tuple(verified))


CleanupAction = Literal["remove", "deferred", "skip"]


@dataclass(frozen=True, slots=True)
class CleanupRow:
    repo: str
    worktree: str
    branch: str
    target_branch: str
    action: CleanupAction
    reason: str


@dataclass(frozen=True, slots=True)
class CleanupPlan:
    rows: tuple[CleanupRow, ...]
    refusal: str | None


def _worktree_list(repo: Path) -> tuple[tuple[str, str], ...] | None:
    """Read `(canonical checkout, checked-out branch)` without changing Git state."""
    listed = probe_git(repo, "worktree", "list", "--porcelain")
    if listed.returncode != 0:
        return None
    rows: list[tuple[str, str]] = []
    path: str | None = None
    branch = ""
    for line in (*listed.stdout.splitlines(), ""):
        if not line:
            if path is not None:
                rows.append((path, branch))
            path, branch = None, ""
        elif line.startswith("worktree "):
            if path is not None:
                return None
            path = str(Path(line[len("worktree ") :]).resolve())
        elif line.startswith("branch "):
            branch = line[len("branch ") :].removeprefix("refs/heads/")
    if not rows or len({path for path, _ in rows}) != len(rows):
        return None
    return tuple(rows)


def plan_finish_cleanup(layout: WorkspaceLayout, path: str, *, runner_cwd: Path | None) -> CleanupPlan:
    """Plan removal of resolved, receipted stamps from read-only Git observations."""
    items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    by_path = {item.path: item for item in items}
    item = by_path.get(path)
    if item is None:
        return CleanupPlan((), f"{path}: unknown work item")
    if item.work_status != "resolved" or item.phase != "done":
        return CleanupPlan((), f"{path}: not resolved")
    if "repo_stamps" in item.invalid_optional_fields:
        return CleanupPlan((), f"{path}: malformed repo_stamps")
    receipt, entries, error = read_finish_receipt(layout, path)
    if error is not None:
        return CleanupPlan((), error)
    if receipt is None:
        return CleanupPlan((), f"{path}: no finish receipt")

    stamps: list[tuple[str | None, Path | None, str, str]] = []
    try:
        declared = declared_repositories(layout)
        if item.worktree or item.branch:
            own = resolve_item_repo(layout, item, by_path)
            stamps.append((own.name, own.path, item.worktree or "", item.branch or ""))
        for name, stamp in sorted(item.repo_stamps.items()):
            if name == WORKSPACE_REPO:
                ws_repo, _note = workspace_repo(layout)
                repo = ws_repo.path if ws_repo is not None else None
            else:
                repo = declared.get(name)
            stamps.append((name, repo, stamp.worktree, stamp.branch))
    except WorkspaceError as exc:
        return CleanupPlan((), str(exc))
    targets = {entry.repo: entry.target_branch for entry in entries}
    for stamp_name, repo, _worktree, _branch in stamps:
        if stamp_name is None or repo is None:
            return CleanupPlan((), f"{path}: stamped repo {stamp_name!r} has no verified checkout")
        if stamp_name not in targets:
            return CleanupPlan((), f"{path}: finish receipt does not name {stamp_name!r}")

    live = [other for other in items if other.path != path and other.work_status not in TERMINAL_STATUSES]
    if any("repo_stamps" in other.invalid_optional_fields for other in live):
        return CleanupPlan((), f"{path}: live item has malformed repo_stamps; shared checkout cannot be ruled out")
    live_worktrees = {
        str(Path(worktree).resolve())
        for other in live
        for worktree in (other.worktree, *(stamp.worktree for stamp in other.repo_stamps.values()))
        if worktree
    }
    live_branches: set[tuple[str | None, str]] = set()
    for other in live:
        if other.branch:
            try:
                owner = resolve_item_repo(layout, other, by_path).name
            except WorkspaceError:
                owner = None
            live_branches.add((owner, other.branch))
        live_branches.update((name, stamp.branch) for name, stamp in other.repo_stamps.items() if stamp.branch)

    runner = runner_cwd.resolve() if runner_cwd is not None else None
    rows: list[CleanupRow] = []
    for stamp_name, repo, worktree, branch in stamps:
        assert stamp_name is not None and repo is not None
        target = targets[stamp_name]
        listed = _worktree_list(repo)
        if listed is None:
            return CleanupPlan((), f"{path}: cannot verify worktrees in {stamp_name!r}")
        listed_paths = {checkout for checkout, _ in listed}
        tree = str(Path(worktree).resolve()) if worktree else ""
        if tree not in listed_paths:
            tree = ""
        if branch:
            exists = probe_git(repo, "show-ref", "--verify", "--quiet", "refs/heads/" + branch)
            if exists.cause != "ok" or exists.returncode not in (0, 1):
                return CleanupPlan((), f"{path}: cannot verify branch {branch!r} in {stamp_name!r}")
            if exists.returncode == 1:
                branch = ""
            elif _commit(repo, "refs/heads/" + branch) is None:
                return CleanupPlan((), f"{path}: cannot verify branch commit {branch!r} in {stamp_name!r}")
        if not tree and not branch:
            continue
        trunk = {str(repo.resolve()), listed[0][0]}
        target_checkouts = {checkout for checkout, checked_branch in listed if checked_branch == target}
        # A stamp whose checkout moved to another branch is stale; do not offer
        # either that checkout or its separately existing branch for removal.
        checkout_branch = next((checked_branch for checkout, checked_branch in listed if checkout == tree), None)
        stale_checkout = bool(tree and branch and checkout_branch not in (branch, ""))
        branch_elsewhere = bool(
            branch and any(checked_branch == branch and checkout != tree for checkout, checked_branch in listed)
        )
        action: CleanupAction
        reason: str
        if branch:
            ancestry = probe_git(repo, "merge-base", "--is-ancestor", "refs/heads/" + branch, "refs/heads/" + target)
            if ancestry.cause != "ok" or ancestry.returncode not in (0, 1):
                return CleanupPlan((), f"{path}: cannot verify merge of {branch!r} into {target!r} in {stamp_name!r}")
        else:
            ancestry = None
        if ancestry is not None and ancestry.returncode == 1:
            action, reason = "skip", "unmerged"
        elif branch == target or (tree and (tree in trunk or tree in target_checkouts)):
            action, reason = "skip", "target"
        elif (
            stale_checkout
            or branch_elsewhere
            or (tree and tree in live_worktrees)
            or (branch and ((stamp_name, branch) in live_branches or (None, branch) in live_branches))
        ):
            action, reason = "skip", "shared"
        elif runner is not None and tree and runner.is_relative_to(tree):
            action, reason = "deferred", "runner"
        else:
            action, reason = "remove", ""
        rows.append(CleanupRow(stamp_name, tree, branch, target, action, reason))
    return CleanupPlan(tuple(rows), None)


def finish_read_guard(layout: WorkspaceLayout, path: str, *, bundle: Bundle | None = None) -> str:
    """Bind owner, ancestors, repo configuration and receipt to their raw preimages."""
    bundle = bundle if bundle is not None else load_bundle(layout.bundle_dir, ignore=IGNORE)
    item = next((i for i in load_items(bundle) if i.path == path), None)
    digest = hashlib.sha256()
    members = (path, *item.ancestor_paths) if item is not None else (path,)
    for member in members:
        doc = bundle.concepts.get(member)
        digest.update(repr((member, doc.serialize() if doc is not None else None)).encode("utf-8"))
    paths = (
        layout.manifest_path,
        layout.manifest_path.with_name("workspace.local.yaml"),
        artifact_ref(path, MANAGED_ARTIFACTS["finish-receipt"]).path(bundle.root),
    )
    for filename in paths:
        try:
            raw = filename.read_bytes()
        except FileNotFoundError:
            raw = None
        digest.update(repr((str(filename), raw)).encode("utf-8"))
    return digest.hexdigest()

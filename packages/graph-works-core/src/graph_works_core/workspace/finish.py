"""Complete repository-local finish targets shared by attended and relay routing."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from okf_io import Bundle, Document, load_bundle, parse
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref

from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.provenance import probe_git
from graph_works_core.workspace.repo_context import RepositoryContext, observe_repository
from graph_works_core.workspace.repos import ItemRepo, declared_repositories, resolve_item_repo


@dataclass(frozen=True, slots=True)
class FinishTarget:
    repo: ItemRepo
    worktree: str
    source_branch: str
    target_branch: str


@dataclass(frozen=True, slots=True)
class FinishPlan:
    targets: tuple[FinishTarget, ...]
    blockers: tuple[str, ...]


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
    if "repo_stamps" in item.invalid_optional_fields:
        blockers.append(f"{path}: repair malformed repo_stamps before finishing")
    if single_repo is not None and item.repo_stamps:
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
        if item.branch or item.worktree:
            candidates.append((single_repo or resolve_item_repo(layout, item, by_path), item.worktree, item.branch))
        elif not item.repo_stamps and item.type not in {"Epic", "Release"}:
            # Main-mode leaves may finish without owning a dedicated branch.
            # The declared checkout is the compatibility location, but its
            # current branch must be observed rather than inferred from trunk.
            own_repo = single_repo or resolve_item_repo(layout, item, by_path)
            if own_repo.path is None:
                blockers.append(f"{path}: no repository checkout can be verified for unstamped finish")
            else:
                checkout = str(own_repo.path.resolve())
                context = context_for(own_repo)
                branches = [branch for branch, paths in context.inventory.items() if checkout in paths]
                if len(branches) != 1:
                    blockers.append(f"{path}: cannot verify current branch of unstamped finish checkout {checkout!r}")
                else:
                    candidates.append((own_repo, checkout, branches[0]))
        for name, stamp in sorted(item.repo_stamps.items()):
            if name not in declared:
                blockers.append(f"{path}: stamped repo {name!r} is not declared")
            else:
                candidates.append((ItemRepo(name, declared[name], "frontmatter"), stamp.worktree, stamp.branch))
        outer = enclosing_owner(item, by_path)
        outer_repo = (single_repo or resolve_item_repo(layout, outer, by_path)) if outer is not None else None
    except WorkspaceError as exc:
        return FinishPlan((), (str(exc),))
    for repo, worktree, branch in candidates:
        if repo.path is None or not worktree or not branch:
            blockers.append(f"{path}: repair incomplete finish stamp for {repo.name!r}")
            continue
        worktree = str(Path(worktree).resolve())
        context = context_for(repo)
        if (
            not context.identity_known
            or not context.inventory_known
            or context.inventory.get(branch) != (worktree,)
            or context.path_exists.get(worktree) is not True
            or context.checkout_usable_by_path.get(worktree) is not True
        ):
            blockers.append(f"{path}: cannot verify clean worktree {worktree!r} on branch {branch!r} in {repo.name!r}")
            continue
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
                blockers.append(
                    f"{path}: prepare enclosing integration anchor {outer.path} in {repo.name!r} before finish"
                )
                continue
            anchor_path = str(Path(anchor_path).resolve())
            if (
                "repo_stamps" in outer.invalid_optional_fields
                or context.inventory.get(target) != (anchor_path,)
                or context.path_exists.get(anchor_path) is not True
                or context.checkout_usable_by_path.get(anchor_path) is not True
            ):
                blockers.append(f"{path}: repair enclosing integration anchor {outer.path} in {repo.name!r}")
                continue
        if not target or not context.branches_known or target not in context.branches:
            blockers.append(f"{path}: cannot verify target branch {target!r} in {repo.name!r}")
            continue
        targets.append(FinishTarget(repo, worktree, branch, target))
    return FinishPlan(tuple(targets), tuple(blockers))


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
    resolved = verified[0].result_commit if verified else None
    return FinishVerification(not blockers, resolved, tuple(blockers), tuple(verified))


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

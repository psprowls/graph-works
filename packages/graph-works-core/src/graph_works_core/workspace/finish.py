"""Complete repository-local finish targets shared by attended and relay routing."""

from __future__ import annotations

import functools
import hashlib
import os
import re
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Concatenate, Literal, cast

from config_io import StoreValidationError
from okf_io import Bundle, Document, load_bundle, parse
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

from graph_works_core.workspace.errors import WorkspaceConfigError, WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import workspace_store
from graph_works_core.workspace.provenance import (
    GitExecutable,
    GitFailure,
    GitOutcome,
    gate_git,
    probe_git,
    resolve_git,
)
from graph_works_core.workspace.repo_context import RepositoryContext, observe_repository
from graph_works_core.workspace.repos import ItemRepo, declared_repositories, resolve_item_repo
from graph_works_core.workspace.workspace_branch import WORKSPACE_REPO, workspace_repo

_PROBE_TIMEOUT = 5  # provenance._GIT_TIMEOUT_SECONDS, copied rather than importing a private name


#: The git the current finish operation runs, set by `@finish_toolchain` at each layout-taking entry point.
_TOOLCHAIN_GIT: ContextVar[GitExecutable | GitFailure | None] = ContextVar("finish_toolchain_git", default=None)


def finish_toolchain[**P, R](
    fn: Callable[Concatenate[WorkspaceLayout, P], R],
) -> Callable[Concatenate[WorkspaceLayout, P], R]:
    """Run *fn* (whose first argument is the layout) with `toolchain.git` as every finish git call's executable.

    The gate resolves its git with `provenance.gate_git`; finish, integrate,
    squash verification and cleanup must run the same one, or a workspace that
    pins `toolchain.git` would gate with one git and integrate with another.
    """

    @functools.wraps(fn)
    def wrapper(layout: WorkspaceLayout, /, *args: P.args, **kwargs: P.kwargs) -> R:
        token = _TOOLCHAIN_GIT.set(gate_git(layout))
        try:
            return fn(layout, *args, **kwargs)
        finally:
            _TOOLCHAIN_GIT.reset(token)

    return wrapper


def finish_git(cwd: Path, *args: str, timeout: float = _PROBE_TIMEOUT) -> GitOutcome:
    """`probe_git` through the gate's resolver; a resolver failure is a non-ok outcome.

    Every finish-evidence git call goes through here, so the executable policy
    (bug-pipeline-gates-fail-open's resolver, `toolchain.git` included when a
    `@finish_toolchain` entry point is running) is decided in exactly one place.
    """
    resolved = _TOOLCHAIN_GIT.get() or resolve_git(None, environ=os.environ)
    if not isinstance(resolved, GitExecutable):
        cause: Literal["missing", "timeout", "error"] = (
            "timeout" if resolved.cause == "timeout" else "error" if resolved.cause == "error" else "missing"
        )
        return GitOutcome(None, "", cause, resolved.detail)
    return probe_git(cwd, *args, executable=resolved.path, timeout=timeout)


def merge_tree_unsupported(repo: Path) -> str | None:
    """None when this git can verify a squash (`merge-tree --write-tree`, git >= 2.38); else why not."""
    probe = finish_git(repo, "merge-tree", "--write-tree", "HEAD", "HEAD")
    if probe.returncode == 0:
        return None
    version = finish_git(repo, "--version").stdout.strip() or "git version unknown"
    return f"squash verification needs `git merge-tree --write-tree` (git >= 2.38); this is {version}"


IntegrationStrategy = Literal["squash", "merge", "ff"]
ReceiptStrategy = Literal["squash", "merge", "ff", "ancestry", "attested"]
ReceiptEvidence = Literal["verified", "accepted"]
#: The relay lists these in this order after the target's default.
STRATEGIES: tuple[IntegrationStrategy, ...] = ("squash", "merge", "ff")


@dataclass(frozen=True, slots=True)
class FinishTarget:
    repo: ItemRepo
    worktree: str  # source checkout the finish worker runs in
    source_branch: str
    target_branch: str
    target_worktree: str | None  # unique checkout holding target_branch, when one exists
    default_strategy: IntegrationStrategy | None = None  # None only for _workspace


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


def finish_strategy(
    layout: WorkspaceLayout, repo_name: str
) -> tuple[IntegrationStrategy, Literal["config", "default"]]:
    """`repositories.<name>.finish.strategy` (layered, local overlay first), else `squash`.

    Read from the explicit mapping rather than a dotted catalog key so a
    repository name containing `.` still resolves; the catalog entry exists
    for `gw config` and documentation.
    """
    try:
        explicit = workspace_store(layout).read_explicit()
    except StoreValidationError as exc:
        raise WorkspaceConfigError(str(exc)) from exc
    repos = explicit.get("repositories")
    entry = repos.get(repo_name) if isinstance(repos, dict) else None
    block = entry.get("finish") if isinstance(entry, dict) else None
    where = f"{layout.manifest_path}: repositories.{repo_name}.finish"
    if block is None:
        return "squash", "default"
    if not isinstance(block, dict) or set(block) - {"strategy"}:
        raise WorkspaceConfigError(f"{where}: expects a mapping with only `strategy`, got {block!r}")
    value = block.get("strategy")
    if value is None:
        return "squash", "default"
    if not isinstance(value, str) or value not in STRATEGIES:
        raise WorkspaceConfigError(f"{where}.strategy: must be one of {sorted(STRATEGIES)}, got {value!r}")
    return value, "config"


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
    strategies: dict[str, IntegrationStrategy] = {}
    try:
        declared = declared_repositories(layout)
        strategies = {name: finish_strategy(layout, name)[0] for name in declared}
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
        default = None if repo.name == WORKSPACE_REPO else strategies.get(repo.name or "", "squash")
        targets.append(FinishTarget(repo, worktree, branch, target, target_worktree, default))
    return FinishPlan(
        tuple(targets), tuple(blockers), FinishOccupancy(tuple(sorted(occupied)), occupancy_known and bool(candidates))
    )


_RECEIPT_STRATEGIES = frozenset({*STRATEGIES, "ancestry", "attested"})
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_OPTIONAL_KEYS = ("target_before", "accepted_by", "reason")


@dataclass(frozen=True, slots=True)
class VerifiedIntegration:
    repo: str
    source_branch: str
    source_commit: str
    target_branch: str
    result_commit: str
    # v2: how the result was produced and whose word it rests on. A v1 entry
    # reads as ancestry that gw verified.
    strategy: ReceiptStrategy = "ancestry"
    evidence: ReceiptEvidence = "verified"
    target_before: str | None = None  # target tip captured before squash/merge/ff
    accepted_by: str | None = None  # attested entries only
    reason: str | None = None  # attested entries only


def receipt_entry_data(entry: VerifiedIntegration) -> dict[str, str]:
    """The v2 mapping written for *entry*; absent optional fields are omitted, never null."""
    return {key: value for key, value in asdict(entry).items() if value is not None}


def _parse_entry(value: object) -> VerifiedIntegration | str:
    """One `integrations[]` entry, or the refusal message that makes the receipt malformed."""
    keys = ("repo", "source_branch", "source_commit", "target_branch", "result_commit")
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k] for k in keys):
        return "malformed finish receipt integration"
    if not all(_SHA.fullmatch(value[k]) for k in ("source_commit", "result_commit")):
        return "malformed finish receipt commit"
    strategy = value.get("strategy", "ancestry")
    evidence = value.get("evidence", "verified")
    raw_before, raw_by, raw_reason = (value.get(k) for k in _OPTIONAL_KEYS)
    if not isinstance(strategy, str) or strategy not in _RECEIPT_STRATEGIES or evidence not in ("verified", "accepted"):
        return "malformed finish receipt integration"
    attested = strategy == "attested"
    if attested != (evidence == "accepted"):
        return "malformed finish receipt integration"
    if attested:
        if not all(isinstance(v, str) and v.strip() for v in (raw_by, raw_reason)):
            return "malformed finish receipt attribution"
    elif raw_by is not None or raw_reason is not None:
        return "malformed finish receipt attribution"
    if strategy in STRATEGIES:
        if not isinstance(raw_before, str) or not _SHA.fullmatch(raw_before):
            return "malformed finish receipt target_before"
    elif raw_before is not None:
        return "malformed finish receipt target_before"
    return VerifiedIntegration(
        value["repo"],
        value["source_branch"],
        value["source_commit"],
        value["target_branch"],
        value["result_commit"],
        cast(ReceiptStrategy, strategy),
        cast(ReceiptEvidence, evidence),
        raw_before if isinstance(raw_before, str) else None,
        raw_by if isinstance(raw_by, str) else None,
        raw_reason if isinstance(raw_reason, str) else None,
    )


@dataclass(frozen=True, slots=True)
class FinishVerification:
    complete: bool
    resolved_in: str | None
    blockers: tuple[str, ...]
    entries: tuple[VerifiedIntegration, ...]
    accepted: tuple[str, ...] = ()


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
            or data.get("receipt_version") not in (1, 2)
            or data.get("owner") != path
            or not isinstance(values, list)
        ):
            return None, (), "malformed finish receipt"
        entries: list[VerifiedIntegration] = []
        for value in values:
            parsed = _parse_entry(value)
            if isinstance(parsed, str):
                return None, (), parsed
            if any(e.repo == parsed.repo for e in entries):
                return None, (), "duplicate finish receipt repository"
            entries.append(parsed)
        return doc, tuple(entries), None
    except (UnicodeError, ValueError):
        return None, (), "malformed finish receipt"


def _commit(repo: Path, ref: str) -> str | None:
    result = finish_git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and _SHA.fullmatch(sha) else None


def _is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return finish_git(repo, "merge-base", "--is-ancestor", older, newer).returncode == 0


def _parents(repo: Path, sha: str) -> tuple[str, ...] | None:
    listed = finish_git(repo, "rev-list", "--parents", "-n", "1", sha)
    fields = listed.stdout.split()
    return tuple(fields[1:]) if listed.returncode == 0 and fields and fields[0] == sha else None


def _tree(repo: Path, ref: str) -> str | None:
    result = finish_git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{tree}")
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and _SHA.fullmatch(sha) else None


def _merged_tree(repo: Path, base: str, source: str) -> str | None:
    """Git's clean merge of *source* into *base* (plain `merge-tree`), or None on conflict or failure."""
    merged = finish_git(repo, "merge-tree", "--write-tree", base, source)
    first = merged.stdout.splitlines()[0] if merged.stdout else ""
    return first if merged.returncode == 0 and _SHA.fullmatch(first) else None


def integration_shape(repo: Path, entry: VerifiedIntegration) -> str | None:
    """None when *entry*'s result has the shape its strategy claims; otherwise why not.

    Never checks the live target tip: historical evidence must stay genuine
    after the target moves on.
    """
    if _commit(repo, entry.source_commit) != entry.source_commit:
        return "source commit is not in the repository"
    if _commit(repo, entry.result_commit) != entry.result_commit:
        return "result commit is not in the repository"
    if entry.strategy == "attested":
        return None
    if entry.strategy == "ancestry":
        return (
            None
            if _is_ancestor(repo, entry.source_commit, entry.result_commit)
            else "source is not an ancestor of the result"
        )
    if entry.strategy == "ff":
        return None if entry.result_commit == entry.source_commit else "fast-forward result is not the source commit"
    before = entry.target_before
    assert before is not None  # the reader requires it for squash/merge/ff
    parents = _parents(repo, entry.result_commit)
    if entry.strategy == "merge":
        return (
            None
            if parents == (before, entry.source_commit)
            else "merge result's parents are not (target_before, source)"
        )
    if parents != (before,):
        return "squash result's parent is not target_before"
    merged = _merged_tree(repo, before, entry.source_commit)
    if merged is None:
        return "merge-tree of target_before and source conflicts or failed"
    if merged != _tree(repo, entry.result_commit):
        return "squash tree differs from merge-tree of target_before and source"
    return None


def historical_integration(target: FinishTarget, entry: VerifiedIntegration) -> bool:
    """Historical evidence may be stale, but must remain a genuine result of its strategy."""
    repo = target.repo.path
    return bool(
        repo is not None
        and entry.repo == target.repo.name
        and entry.source_branch == target.source_branch
        and entry.target_branch == target.target_branch
        and integration_shape(repo, entry) is None
    )


def verify_integration(target: FinishTarget, entry: VerifiedIntegration) -> str | None:
    """Live proof for one target: None when verified, else the reason. Never mutates Git."""
    repo = target.repo.path
    if repo is None or target.repo.name is None:
        return "no repository checkout"
    if (entry.repo, entry.source_branch, entry.target_branch) != (
        target.repo.name,
        target.source_branch,
        target.target_branch,
    ):
        return "entry names a different target"
    source = _commit(repo, "refs/heads/" + target.source_branch)
    tip = _commit(repo, "refs/heads/" + target.target_branch)
    if source is None or tip is None:
        return "cannot resolve the source or target branch"
    if entry.source_commit != source:
        return "source branch moved since the receipt"
    shape = integration_shape(repo, entry)
    if shape is not None:
        return shape
    if not _is_ancestor(repo, entry.result_commit, tip):
        return "result is not reachable from the target tip"
    return None


def observe_integration(target: FinishTarget, entry: VerifiedIntegration | None = None) -> VerifiedIntegration | None:
    """*entry* when it verifies; with no entry, current-tip ancestry evidence. Never mutates Git."""
    if entry is None:
        repo = target.repo.path
        if repo is None or target.repo.name is None:
            return None
        source = _commit(repo, "refs/heads/" + target.source_branch)
        tip = _commit(repo, "refs/heads/" + target.target_branch)
        if source is None or tip is None:
            return None
        entry = VerifiedIntegration(target.repo.name, target.source_branch, source, target.target_branch, tip)
    return entry if verify_integration(target, entry) is None else None


#: How far back rediscovery looks for a landed merge or squash on the target.
REDISCOVERY_SCAN = 200


def _first_parent_log(repo: Path, tip: str, *exclude: str) -> list[list[str]] | None:
    listed = finish_git(
        repo,
        "rev-list",
        "--first-parent",
        "--parents",
        f"--max-count={REDISCOVERY_SCAN}",
        tip,
        *(f"^{e}" for e in exclude),
    )
    return [line.split() for line in listed.stdout.splitlines() if line] if listed.returncode == 0 else None


def discover_integration(target: FinishTarget) -> VerifiedIntegration | str:
    """Rediscover how the current source landed on the target, or say why not. Never mutates Git.

    Reachable source: a first-parent merge commit whose second parent is the
    source is `merge`; anything else (including a landed fast-forward, which
    Git cannot tell from prior ancestry) is `ancestry`. Unreachable source:
    exactly one non-empty single-parent first-parent commit whose tree equals
    `merge-tree(parent, source)` is `squash`.
    """
    repo = target.repo.path
    if repo is None or target.repo.name is None:
        return "no repository checkout"
    source = _commit(repo, "refs/heads/" + target.source_branch)
    tip = _commit(repo, "refs/heads/" + target.target_branch)
    if source is None or tip is None:
        return "cannot resolve the source or target branch"
    name, branch, into = target.repo.name, target.source_branch, target.target_branch
    if _is_ancestor(repo, source, tip):
        for fields in _first_parent_log(repo, tip) or []:
            if len(fields) == 3 and fields[2] == source:
                return VerifiedIntegration(name, branch, source, into, fields[0], "merge", "verified", fields[1])
        return VerifiedIntegration(name, branch, source, into, tip)
    base = finish_git(repo, "merge-base", source, tip).stdout.strip()
    log = _first_parent_log(repo, tip, *([base] if _SHA.fullmatch(base) else []))
    if log is None:
        return "cannot read target history"
    matches = [
        candidate
        for fields in log
        if len(fields) == 2 and _tree(repo, fields[0]) != _tree(repo, fields[1])
        for candidate in (VerifiedIntegration(name, branch, source, into, fields[0], "squash", "verified", fields[1]),)
        if integration_shape(repo, candidate) is None
    ]
    if len(matches) > 1:
        return f"ambiguous: {len(matches)} squash commits on {into} match {branch}; record with accept-integration"
    if matches:
        return matches[0]
    return f"no merge, fast-forward or squash of {branch} found in the last {REDISCOVERY_SCAN} commits of {into}"


@finish_toolchain
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
    accepted: list[str] = []
    for target in plan.targets:
        entry = next((e for e in entries if e.repo == target.repo.name), None)
        reason = "no receipt entry" if entry is None else verify_integration(target, entry)
        if reason is not None:
            blockers.append(f"{target.repo.name}: incomplete integration into {target.target_branch} ({reason})")
            continue
        assert entry is not None
        verified.append(entry)
        if entry.evidence == "accepted":
            accepted.append(entry.repo)
    names = {target.repo.name for target in plan.targets}
    if any(e.repo not in names for e in entries):
        blockers.append("finish receipt contains an unexpected repository")
    if not plan.targets:
        blockers.append("no verified finish targets")
    code_entries = [entry for entry in verified if entry.repo != WORKSPACE_REPO]
    resolved = (code_entries or verified)[0].result_commit if verified else None
    return FinishVerification(not blockers, resolved, tuple(blockers), tuple(verified), tuple(accepted))


CleanupAction = Literal["remove", "deferred", "skip"]


CleanupDelete = Literal["branch-d", "update-ref"]


@dataclass(frozen=True, slots=True)
class CleanupRow:
    repo: str
    worktree: str
    branch: str
    target_branch: str
    action: CleanupAction
    reason: str
    # How the branch is deleted: `git branch -d` when ancestry proves the
    # merge; compare-and-delete to `expected` when a verified squash does.
    delete: CleanupDelete = "branch-d"
    expected: str | None = None


def squash_integrated(repo: Path, entry: VerifiedIntegration) -> bool:
    """A verified squash entry whose result is still on its target branch."""
    tip = _commit(repo, "refs/heads/" + entry.target_branch)
    return (
        entry.strategy == "squash"
        and entry.evidence == "verified"
        and tip is not None
        and integration_shape(repo, entry) is None
        and _is_ancestor(repo, entry.result_commit, tip)
    )


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


@finish_toolchain
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
    by_repo = {entry.repo: entry for entry in entries}
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
        stamped_branch = branch
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
        unmerged: bool
        entry = by_repo[stamp_name]
        delete: CleanupDelete = "branch-d"
        expected: str | None = None
        if branch:
            ancestry = probe_git(repo, "merge-base", "--is-ancestor", "refs/heads/" + branch, "refs/heads/" + target)
            if ancestry.cause != "ok" or ancestry.returncode not in (0, 1):
                return CleanupPlan((), f"{path}: cannot verify merge of {branch!r} into {target!r} in {stamp_name!r}")
            unmerged = ancestry.returncode == 1
            # A squash never makes the branch an ancestor; a verified squash
            # receipt at the branch's exact tip is the proof instead.
            if (
                unmerged
                and _commit(repo, "refs/heads/" + branch) == entry.source_commit
                and squash_integrated(repo, entry)
            ):
                unmerged, delete, expected = False, "update-ref", entry.source_commit
        elif tree and stamped_branch:
            # The stamped branch is gone. A deleted branch alone is not proof the
            # surviving checkout is safe: its own identity must still hold — a
            # checkout switched to an unrelated named branch is not this stamp
            # any more.
            unmerged = bool(checkout_branch and checkout_branch != target)
        else:
            unmerged = False
        # A detached checkout can move independently of its stamped branch.
        # Prove its HEAD even when that branch still exists and is merged.
        if tree and not checkout_branch:
            head = _commit(Path(tree), "HEAD")
            if head is None:
                return CleanupPlan((), f"{path}: cannot verify checkout HEAD in {stamp_name!r}")
            head_ancestry = probe_git(repo, "merge-base", "--is-ancestor", head, "refs/heads/" + target)
            if head_ancestry.cause != "ok" or head_ancestry.returncode not in (0, 1):
                return CleanupPlan(
                    (), f"{path}: cannot verify merge of checkout HEAD into {target!r} in {stamp_name!r}"
                )
            head_merged = head_ancestry.returncode == 0 or (
                head == entry.source_commit and squash_integrated(repo, entry)
            )
            unmerged = unmerged or not head_merged
        if unmerged:
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
        rows.append(CleanupRow(stamp_name, tree, branch, target, action, reason, delete, expected))
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

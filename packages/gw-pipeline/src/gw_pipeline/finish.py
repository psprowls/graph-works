"""Complete repository-local finish targets shared by attended and relay routing."""

from __future__ import annotations

import functools
import hashlib
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Concatenate, Literal, cast

from config_io import StoreValidationError
from gw_kernel.workspace.bundle import load_work_bundle, load_workspace_bundle
from gw_kernel.workspace.errors import WorkspaceConfigError, WorkspaceError
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.manifest import workflow_store, workspace_store
from gw_kernel.workspace.provenance import (
    GitExecutable,
    GitFailure,
    GitOutcome,
    gate_git,
    probe_git,
    resolve_git,
)
from gw_kernel.workspace.repos import ItemRepo, resolve_item_repo
from okf_io import Bundle, Document, parse
from work_tracker_okf.affects import code_affects
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref

from gw_pipeline.placement import active_path
from gw_pipeline.placement_resolve import item_placement
from gw_pipeline.repo_context import RepositoryContext, observe_repository

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
    target_worktree: str  # the unique checkout holding target_branch
    default_strategy: IntegrationStrategy = "squash"


class FinishRefused(Exception):
    """A finish operation refused: *refusal* is the short code, *detail* the sentence naming the fix."""

    def __init__(self, refusal: str, detail: str, conflicts: tuple[str, ...] = ()) -> None:
        super().__init__(f"{refusal}: {detail}")
        self.refusal = refusal
        self.detail = detail
        self.conflicts = conflicts


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


def finish_strategies(layout: WorkspaceLayout, names: Iterable[str]) -> dict[str, IntegrationStrategy]:
    """`repositories.<name>.finish.strategy` for each of *names* (layered, local overlay first), else `squash`.

    One `read_explicit()` serves every name, and only the named repositories'
    blocks are validated: a malformed block elsewhere does not block them.
    Read from the explicit mapping rather than a dotted catalog key so a
    repository name containing `.` still resolves.
    """
    wanted = tuple(dict.fromkeys(names))
    try:
        explicit = workspace_store(layout).read_explicit()
    except StoreValidationError as exc:
        raise WorkspaceConfigError(str(exc)) from exc
    repos = explicit.get("repositories")
    out: dict[str, IntegrationStrategy] = {}
    for repo_name in wanted:
        entry = repos.get(repo_name) if isinstance(repos, dict) else None
        block = entry.get("finish") if isinstance(entry, dict) else None
        where = f"{layout.manifest_path}: repositories.{repo_name}.finish"
        if block is None:
            out[repo_name] = "squash"
            continue
        if not isinstance(block, dict) or set(block) - {"strategy"}:
            raise WorkspaceConfigError(f"{where}: expects a mapping with only `strategy`, got {block!r}")
        value = block.get("strategy")
        if value is None:
            out[repo_name] = "squash"
        elif not isinstance(value, str) or value not in STRATEGIES:
            raise WorkspaceConfigError(f"{where}.strategy: must be one of {sorted(STRATEGIES)}, got {value!r}")
        else:
            out[repo_name] = value
    return out


def finish_strategy(
    layout: WorkspaceLayout, repo_name: str
) -> tuple[IntegrationStrategy, Literal["config", "default"]]:
    """One repository's strategy and whether it came from `config` or the `default`."""
    strategy = finish_strategies(layout, (repo_name,))[repo_name]
    explicit = workspace_store(layout).read_explicit().get("repositories")
    entry = explicit.get(repo_name) if isinstance(explicit, dict) else None
    block = entry.get("finish") if isinstance(entry, dict) else None
    configured = isinstance(block, dict) and block.get("strategy") is not None
    return strategy, "config" if configured else "default"


def _target_repo_names(
    layout: WorkspaceLayout, item: WorkItem, by_path: Mapping[str, WorkItem]
) -> tuple[tuple[str, ...], str | None]:
    """The repositories *item* finishes into, own repository first (design section 5)."""
    names: list[str] = []
    own_name: str | None = None
    if code_affects(item.affects):
        own = resolve_item_repo(layout, item, by_path)
        if own.name is not None and own.path is not None:
            own_name = own.name
            names.append(own.name)
    if item.type in {"Epic", "Release"} or (item.type == "Feature" and item.child_paths):
        for descendant in by_path.values():
            if item.path in descendant.ancestor_paths and descendant.work_status == "resolved":
                _doc, entries, _error = read_finish_receipt(layout, descendant.path)
                names.extend(entry.repo for entry in entries)
    return tuple(dict.fromkeys(names)), own_name


def resolve_finish_targets(
    layout: WorkspaceLayout,
    items: Sequence[WorkItem],
    path: str,
    *,
    repo_contexts: Mapping[str, RepositoryContext] = MappingProxyType({}),
    checkouts: Literal["required", "ignored"] = "required",
) -> FinishPlan:
    """One target per code repository the item finishes into, from computed placement; no Git mutation.

    The orchestration shell may supply its already-observed `repo_contexts` to
    avoid probing those repositories twice.

    `checkouts="ignored"` is for ref-only consumers (inspecting or accepting
    evidence): it suppresses the "not a usable checkout" and "unique holder"
    blockers, so a removed or pruned worktree still yields a target carrying the
    computed paths. The source and target branch refs must still exist.
    Mutating consumers keep the default `"required"`.
    """
    strict = checkouts == "required"
    by_path = {item.path: item for item in items}
    item = by_path[path]
    targets: list[FinishTarget] = []
    blockers: list[str] = []
    occupied: set[str] = set()
    occupancy_known = True
    observed = dict(repo_contexts)
    try:
        names, own_name = _target_repo_names(layout, item, by_path)
        strategies = finish_strategies(layout, names)
        placements = {
            name: item_placement(layout, item, by_path, repo_name=None if name == own_name else name) for name in names
        }
    except WorkspaceError as exc:
        return FinishPlan((), (str(exc),))
    repos: list[str] = []
    for name in names:
        p = placements[name]
        if p is None:
            continue
        repos.append(name)
        root = str(p.repo_root.resolve())
        context = next((c for c in observed.values() if root in c.checkout_usable_by_path), None)
        if context is None:
            context = observe_repository(p.repo_root, paths=[p.worktree, p.parent_worktree])
            observed[context.identity] = context
        worktree = str(p.worktree.resolve())
        parent = str(p.parent_worktree.resolve())
        target = p.parent_branch
        holders = context.inventory.get(target, ())
        if context.identity_known and context.inventory_known and holders:
            occupied.update(holders)
        else:
            occupancy_known = False
        place_fix = f"run gw work place {path}"
        if not context.branches_known or p.branch not in context.branches:
            blockers.append(f"{path}: source branch {p.branch!r} is missing in {name!r}; {place_fix}")
            continue
        if strict and (
            not context.identity_known
            or not context.inventory_known
            or context.inventory.get(p.branch) != (worktree,)
            or context.path_exists.get(worktree) is not True
            or context.checkout_usable_by_path.get(worktree) is not True
        ):
            blockers.append(
                f"{path}: worktree {worktree!r} is not a usable checkout of {name!r}"
                f" on branch {p.branch!r}; {place_fix}"
            )
            continue
        if target not in context.branches:
            blockers.append(f"{path}: target branch {target!r} is missing in {name!r}")
            continue
        if strict and holders != (parent,):
            if p.owner_path is not None:
                fix = f"run gw work place {p.owner_path}"
                blockers.append(
                    f"{path}: enclosing worktree {parent!r} is not the unique holder of {target!r} in {name!r}; {fix}"
                )
            else:
                held = ", ".join(holders) if holders else "nothing"
                blockers.append(
                    f"{path}: declared checkout {parent!r} must be the unique holder of {target!r} in {name!r};"
                    f" {target!r} is held by {held}"
                )
            continue
        targets.append(
            FinishTarget(
                ItemRepo(name, p.repo_root, "frontmatter"), worktree, p.branch, target, parent, strategies[name]
            )
        )
    return FinishPlan(
        tuple(targets), tuple(blockers), FinishOccupancy(tuple(sorted(occupied)), occupancy_known and bool(repos))
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
            # The archive move carries the receipt along without rewriting its owner,
            # so an archived item's receipt still names the active path.
            or data.get("owner") not in {path, active_path(path)}
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


def resolve_commit(repo: Path, ref: str) -> str | None:
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


def integration_shape(
    repo: Path,
    entry: VerifiedIntegration,
    history: Mapping[str, tuple[tuple[str, ...], str]] | None = None,
) -> str | None:
    """None when *entry*'s result has the shape its strategy claims; otherwise why not.

    Never checks the live target tip: historical evidence must stay genuine
    after the target moves on. *history* maps already-read commits to
    `(parents, tree)`; a result found there needs no further git process
    except `merge-tree`, and the caller vouches for the source commit.
    """
    known = history.get(entry.result_commit) if history else None
    if known is None:
        if resolve_commit(repo, entry.source_commit) != entry.source_commit:
            return "source commit is not in the repository"
        if resolve_commit(repo, entry.result_commit) != entry.result_commit:
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
    parents = known[0] if known is not None else _parents(repo, entry.result_commit)
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
    if merged != (known[1] if known is not None else _tree(repo, entry.result_commit)):
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
    source = resolve_commit(repo, "refs/heads/" + target.source_branch)
    tip = resolve_commit(repo, "refs/heads/" + target.target_branch)
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


#: How far back rediscovery looks for a landed merge or squash on the target.
REDISCOVERY_SCAN = 200


def _first_parent_log(repo: Path, tip: str, *exclude: str) -> list[tuple[str, tuple[str, ...], str]] | None:
    """`(commit, parents, tree)` rows of the target's first-parent history, read in one git process."""
    listed = finish_git(
        repo,
        "rev-list",
        "--first-parent",
        "--parents",
        "--format=%T",
        f"--max-count={REDISCOVERY_SCAN}",
        tip,
        *(f"^{e}" for e in exclude),
    )
    if listed.returncode != 0:
        return None
    rows: list[tuple[str, tuple[str, ...], str]] = []
    lines = listed.stdout.splitlines()
    for head, tree in zip(lines[0::2], lines[1::2], strict=False):
        fields = head.split()
        if len(fields) >= 2 and fields[0] == "commit":
            rows.append((fields[1], tuple(fields[2:]), tree.strip()))
    return rows


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
    source = resolve_commit(repo, "refs/heads/" + target.source_branch)
    tip = resolve_commit(repo, "refs/heads/" + target.target_branch)
    if source is None or tip is None:
        return "cannot resolve the source or target branch"
    name, branch, into = target.repo.name, target.source_branch, target.target_branch
    if _is_ancestor(repo, source, tip):
        for commit, parents, _tree_id in _first_parent_log(repo, tip) or []:
            if len(parents) == 2 and parents[1] == source:
                return VerifiedIntegration(name, branch, source, into, commit, "merge", "verified", parents[0])
        return VerifiedIntegration(name, branch, source, into, tip)
    base = finish_git(repo, "merge-base", source, tip).stdout.strip()
    log = _first_parent_log(repo, tip, *([base] if _SHA.fullmatch(base) else []))
    if log is None:
        return "cannot read target history"
    history = {commit: (parents, tree) for commit, parents, tree in log}
    matches = []
    for commit, parents, tree in log:
        if len(parents) != 1:
            continue
        parent = parents[0]
        parent_tree = history[parent][1] if parent in history else _tree(repo, parent)
        if tree == parent_tree:
            continue
        candidate = VerifiedIntegration(name, branch, source, into, commit, "squash", "verified", parent)
        if integration_shape(repo, candidate, history) is None:
            matches.append(candidate)
    if len(matches) > 1:
        return f"ambiguous: {len(matches)} squash commits on {into} match {branch}; record with accept-integration"
    if matches:
        return matches[0]
    return f"no merge, fast-forward or squash of {branch} found in the last {REDISCOVERY_SCAN} commits of {into}"


@finish_toolchain
def inspect_finish(layout: WorkspaceLayout, path: str) -> FinishVerification:
    """Reverify every recorded integration against live repository-local refs."""
    bundle = load_work_bundle(layout)
    items = load_items(bundle)
    by_path = {item.path: item for item in items}
    if path not in by_path:
        return FinishVerification(False, None, (f"{path}: unknown finish owner",), ())
    plan = resolve_finish_targets(layout, items, path, checkouts="ignored")
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
    resolved = verified[0].result_commit if verified else None
    return FinishVerification(not blockers, resolved, tuple(blockers), tuple(verified), tuple(accepted))


def finish_read_guard(layout: WorkspaceLayout, path: str, *, bundle: Bundle | None = None) -> str:
    """Bind owner, ancestors, repo configuration and receipt to their raw preimages."""
    bundle = bundle if bundle is not None else load_workspace_bundle(layout, ignore=IGNORE)
    item = next((i for i in load_items(bundle) if i.path == path), None)
    digest = hashlib.sha256()
    members = (path, *item.ancestor_paths) if item is not None else (path,)
    for member in members:
        doc = bundle.concepts.get(member)
        digest.update(repr((member, doc.serialize() if doc is not None else None)).encode("utf-8"))
    try:
        store = workflow_store(layout)
        workflow_files: tuple[Path, ...] = (store.base.path, store.overlay.path)
    except WorkspaceError as exc:
        # The guard is a digest, not a validator: an unresolvable workflow file digests its refusal.
        digest.update(repr(("workflow", str(exc))).encode("utf-8"))
        workflow_files = ()
    paths = (
        layout.manifest_path,
        layout.manifest_path.with_name("workspace.local.yaml"),
        *workflow_files,
        artifact_ref(path, MANAGED_ARTIFACTS["finish-receipt"]).path(bundle.root),
    )
    for filename in paths:
        try:
            raw = filename.read_bytes()
        except FileNotFoundError:
            raw = None
        digest.update(repr((str(filename), raw)).encode("utf-8"))
    return digest.hexdigest()

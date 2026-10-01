"""Reference-repository lifecycle commands: `run_repo_add`, `run_repo_restore`, `run_repo_advance`.

Core orchestrates and the lane package owns git (D-005). This module resolves the git executable
(`toolchain.git`, `GW_GIT`, PATH), runs network and clone steps **outside** the bundle lock, and then applies
page writes under `held_bundle_lock` through a `WriteLog`, so a failed write restores every byte. It commits
exactly the written paths with `commit_pending`. No work item is involved, so no decision-owner lock is taken.
Refusals are results, not exceptions (ADR 2026-08-13 command modules). No clock: callers pass `now`.
"""

from __future__ import annotations

import errno
import hashlib
import secrets
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import repositories_okf
from code_graph_io.checkpoint import graph_checkpoint
from config_io import StoreValidationError
from okf_ext.locking import locked
from okf_ext.proposals import apply as apply_proposal
from okf_io import Bundle, Document, append_log_entry, build_link_graph, load, update_index
from repositories_okf.changelog import changelog_path, entries_from_bundle, entry_for, reconcile_changelog
from repositories_okf.flagging import FlaggedPage, flag_pages, plan_flag_proposal
from repositories_okf.git import (
    Git,
    GitFailure,
    HeadState,
    RangeFacts,
    branch_tip,
    commit_facts,
    common_dir,
    current_branch,
    default_branch,
    describe,
    detach,
    fetch,
    has_commit,
    head_state,
    is_clean,
    materialize,
    origin_url,
    range_facts,
    remove_tree,
    resolve_ref,
    rev_count,
    worktree_add,
    worktree_list,
    worktree_remove,
    worktree_root,
)
from repositories_okf.lane import LANE_DIR
from repositories_okf.lifecycle import (
    MANAGED_TYPE,
    NAME_PATTERN,
    checkout_action,
    clone_path,
    default_name,
    lane_pages,
    managed_page_text,
    page_path,
    reference_page_text,
    restore_action,
    scan_config_hash,
    valid_name,
)
from repositories_okf.pin import Generation, Pin, read_pin, write_pin
from repositories_okf.snapshots import Snapshot, render_snapshot, snapshot_path, summary

from graph_works_core.repositories.scan_guard import bundle_changes, discard_bundle_changes
from graph_works_core.repositories.writes import WriteLog
from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.commits import COMMIT_FAILED_PREFIX, CommitOutcome, WorkspaceCommit, commit_target
from graph_works_core.workspace.config import declared_checkouts, load_workspace_config
from graph_works_core.workspace.lane_facts import runner
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import set_value
from graph_works_core.workspace.provenance import run_git
from graph_works_core.workspace.repos import declared_clone, in_bundle_clone
from graph_works_core.workspace.transactions import commit_pending, held_bundle_lock

RefusalCode = Literal[
    "invalid-url",
    "invalid-name",
    "name-taken",
    "remote-unreachable",
    "ref-not-found",
    "git-unavailable",
    "git-failed",
    "not-found",
    "repo-declared",
    "checkout-exists",
    "checkout-foreign",
    "no-checkout",
    "checkout-missing",
    "checkout-dirty",
    "checkout-off-track",
    "clone-not-detached",
    "no-track",
    "to-unsupported",
    "workspace-dirty",
    "workspace-unversioned",
    "scan-failed",
    "no-pin",
    "clone-missing",
    "clone-dirty",
    "url-mismatch",
    "pin-unreachable",
    "write-failed",
    # gw repo adopt (tech-debt-migrate-repositories-into-lane)
    "unknown-repository",
    "local-override",
    "not-a-primary-checkout",
    "already-in-bundle",
    "lane-occupied",
    "not-ignored",
    "worktree-dirty",
    "no-origin",
    "track-mismatch",
    "track-worktree-conflict",
    "track-checked-out",
    "cross-device",
    "cwd-inside-checkout",
    "manifest-unsupported",
]

#: The `generated.by` / proposal `by` stamp for writes this vertical makes.
ACTOR = f"repositories-okf/{repositories_okf.__version__}"


@dataclass(frozen=True, slots=True)
class RepoRefusal:
    code: RefusalCode
    detail: str


def _operation_lock_path(layout: WorkspaceLayout, name: str) -> Path:
    """Clone ownership always precedes bundle ownership; never acquire these locks in reverse."""
    identity = f"{layout.bundle_dir.resolve()}\n{name}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return layout.cache_dir / "repository-operations" / f"{digest}.lock"


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError(f"`now` must be timezone-aware, got {now!r}")


def _instant(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _runner(layout: WorkspaceLayout, environ: Mapping[str, str] | None) -> Git | RepoRefusal:
    resolved = runner(layout, environ)
    return RepoRefusal("git-unavailable", resolved) if isinstance(resolved, str) else resolved


def _git_refusal(failure: GitFailure, *, not_found: RefusalCode, otherwise: RefusalCode = "git-failed") -> RepoRefusal:
    code = not_found if failure.cause == "not-found" else otherwise
    return RepoRefusal(code, f"{failure.command}: {failure.detail}")


def _reconcile_indexes(
    layout: WorkspaceLayout, log: WriteLog, name: str, *, directories: Sequence[str] | None = None
) -> None:
    """Reconcile the lane's, the repository's and its snapshots' `index.md`, so a later `gw wiki index` is a no-op."""
    bundle = load_workspace_bundle(layout)
    if directories is None:
        directories = (LANE_DIR, f"{LANE_DIR}/{name}", f"{LANE_DIR}/{name}/snapshots")
    for update in update_index(bundle, directories=directories, create_missing=True, dry_run=True):
        if update.changed:
            log.write(update.path, update.after)


def _append_log(layout: WorkspaceLayout, log: WriteLog, text: str, *, today: date) -> str | None:
    path = layout.bundle_dir / "log.md"
    if not path.is_file():
        return "log.md is absent; no log entry written"
    appended = append_log_entry(load(path), text, on=today, dry_run=True)
    log.write("log.md", appended.after)
    return None


def _commit(
    layout: WorkspaceLayout,
    subject: str,
    log: WriteLog,
    *,
    extra: Sequence[str] = (),
    root_paths: Sequence[str] = (),
    on_committed: Callable[[], None] | None = None,
) -> tuple[CommitOutcome, tuple[str, ...]]:
    paths = tuple(sorted({*log.paths, *extra}))
    commit = WorkspaceCommit(subject, extra_paths=paths, root_paths=tuple(root_paths))
    outcome = (
        commit_pending(layout, commit, on_committed=on_committed)
        if on_committed is not None
        else commit_pending(layout, commit)
    )
    warnings = (f"{COMMIT_FAILED_PREFIX}{outcome.reason}",) if outcome.status == "failed" else ()
    return outcome, warnings


def _managed_checkout(layout: WorkspaceLayout, name: str) -> Path | None:
    clone = declared_clone(layout, name)
    expected = layout.bundle_dir / clone_path(name)
    if clone is None or not in_bundle_clone(layout, clone) or clone.resolve() != expected.resolve():
        return None
    return declared_checkouts(layout).get(name)


def _relative_to_root(layout: WorkspaceLayout, path: Path) -> str:
    resolved, root = path.resolve(), layout.root.resolve()
    return resolved.relative_to(root).as_posix() if resolved.is_relative_to(root) else resolved.as_posix()


def _entry(op: str, title: str, detail: str) -> str:
    """One §9 bullet, the same shape `gw util log` writes: `**<op>** <title> — <detail>`."""
    return f"**{op}** {title} — {detail}"


@dataclass(frozen=True, slots=True)
class RepoAddResult:
    name: str
    url: str
    track: str | None
    ref: str | None
    commit: str | None
    describe: str | None
    paths: tuple[str, ...]
    dry_run: bool
    refusal: RepoRefusal | None = None
    commit_outcome: CommitOutcome | None = None
    warnings: tuple[str, ...] = ()
    managed: bool = False
    checkout: str | None = None

    @property
    def ok(self) -> bool:
        return self.refusal is None


def _add_paths(name: str, snapshot_rel: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                page_path(name),
                snapshot_rel,
                changelog_path(name),
                f"{LANE_DIR}/index.md",
                f"{LANE_DIR}/{name}/index.md",
                f"{LANE_DIR}/{name}/snapshots/index.md",
                "log.md",
            }
        )
    )


def run_repo_add(
    layout: WorkspaceLayout,
    url: str,
    *,
    name: str | None = None,
    track: str | None = None,
    ref: str | None = None,
    managed: bool = False,
    checkout: Path | None = None,
    now: datetime,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
    nonce: str | None = None,
) -> RepoAddResult:
    """§4.1: resolve, clone outside the lock, then write the page, baseline snapshot, changelog, indexes and
    log, and commit."""
    _require_aware(now)
    if managed and ref is not None:
        raise ValueError("ref= does not apply to a managed repository: it is pinned at the tip of its track")
    if not managed and checkout is not None:
        raise ValueError("checkout= applies only to a managed repository")
    if managed:
        return _add_managed(
            layout,
            url,
            name=name,
            track=track,
            checkout=checkout,
            now=now,
            dry_run=dry_run,
            environ=environ,
            nonce=nonce,
        )
    chosen = name or default_name(url)
    result = RepoAddResult(
        name=chosen, url=url, track=None, ref=None, commit=None, describe=None, paths=(), dry_run=dry_run
    )
    if not url.strip() or url.startswith("-"):
        return replace(result, refusal=RepoRefusal("invalid-url", f"{url!r} is not a repository URL"))
    if not valid_name(chosen):
        return replace(result, refusal=RepoRefusal("invalid-name", f"{chosen!r} must match {NAME_PATTERN.pattern}"))
    directory = layout.bundle_dir / LANE_DIR / chosen
    if (layout.bundle_dir / page_path(chosen)).exists() or directory.exists():
        return replace(
            result, refusal=RepoRefusal("name-taken", f"{page_path(chosen)} or {LANE_DIR}/{chosen}/ already exists")
        )
    git = _runner(layout, environ)
    if isinstance(git, RepoRefusal):
        return replace(result, refusal=git)
    branch = track or default_branch(git, url, cwd=layout.bundle_dir)
    if isinstance(branch, GitFailure):
        return replace(
            result, refusal=_git_refusal(branch, not_found="remote-unreachable", otherwise="remote-unreachable")
        )
    wanted = ref or branch
    commit = resolve_ref(git, url, wanted, cwd=layout.bundle_dir)
    if isinstance(commit, GitFailure):
        return replace(
            result,
            track=branch,
            ref=wanted,
            refusal=_git_refusal(commit, not_found="ref-not-found", otherwise="remote-unreachable"),
        )
    fetched_at = _instant(now)
    result = replace(
        result,
        track=branch,
        ref=wanted,
        commit=commit,
        paths=_add_paths(chosen, snapshot_path(chosen, fetched_at[:10], commit)),
    )
    if dry_run:
        return result

    clone = layout.bundle_dir / clone_path(chosen)
    incoming = layout.cache_dir / "repo-incoming" / f"{chosen}-{nonce or secrets.token_hex(4)}"
    staged = incoming.with_name(incoming.name + "-ready")
    failure = materialize(git, url, staged, commit, incoming=incoming)
    if failure is not None:
        return replace(result, refusal=_git_refusal(failure, not_found="ref-not-found"))
    facts = commit_facts(git, staged, commit)
    if isinstance(facts, GitFailure):
        remove_tree(staged)
        return replace(result, refusal=_git_refusal(facts, not_found="git-failed"))
    described = describe(git, staged, commit)
    pin = Pin(
        commit=commit,
        fetched_at=fetched_at,
        ref=wanted,
        describe=described,
        tree=facts.tree,
        commit_date=facts.commit_date,
    )
    snapshot = Snapshot(
        name=chosen, commit=commit, fetched_at=fetched_at, describe=described, commit_date=facts.commit_date
    )

    log = WriteLog(layout.bundle_dir)
    owned = False
    try:
        with locked(_operation_lock_path(layout, chosen)), held_bundle_lock(layout):
            if (layout.bundle_dir / page_path(chosen)).exists() or directory.exists():
                remove_tree(staged)
                return replace(result, refusal=RepoRefusal("name-taken", f"repository {chosen!r} was added meanwhile"))
            directory.mkdir(parents=True)
            owned = True
            try:
                clone.parent.mkdir(parents=True)
                try:
                    staged.replace(clone)
                except OSError as exc:
                    if exc.errno != errno.EXDEV:
                        raise
                    # Layout overrides may place cache and bundle on separate filesystems.
                    # The repository directory is exclusively ours; copytree reserves its child.
                    shutil.copytree(staged, clone, symlinks=True)
                    remove_tree(staged)
                log.write(page_path(chosen), reference_page_text(name=chosen, url=url, track=branch, pin=pin))
                log.write(snapshot.path, render_snapshot(snapshot))
                log.write(
                    changelog_path(chosen), reconcile_changelog(None, name=chosen, entries=(entry_for(snapshot),))
                )
                _reconcile_indexes(layout, log, chosen)
                log_warning = _append_log(
                    layout,
                    log,
                    _entry("repo-add", chosen, f"{url} @ {snapshot.label}"),
                    today=now.astimezone(UTC).date(),
                )
                outcome, commit_warnings = _commit(layout, f"workspace: add reference repository {chosen}", log)
            except (OSError, ValueError):
                log.rollback()
                raise
    except (OSError, ValueError) as exc:
        remove_tree(staged)
        if owned:
            remove_tree(directory)
        return replace(result, refusal=RepoRefusal("write-failed", str(exc)))
    warnings = (*((log_warning,) if log_warning else ()), *commit_warnings)
    return replace(result, describe=described, paths=log.paths, commit_outcome=outcome, warnings=warnings)


def _declared_names(layout: WorkspaceLayout) -> frozenset[str]:
    try:
        return frozenset(entry.name for entry in load_workspace_config(layout).repos)
    except OSError:
        return frozenset()


def _add_managed(
    layout: WorkspaceLayout,
    url: str,
    *,
    name: str | None,
    track: str | None,
    checkout: Path | None,
    now: datetime,
    dry_run: bool,
    environ: Mapping[str, str] | None,
    nonce: str | None,
) -> RepoAddResult:
    """Resolve the track, link a checkout, and commit the page and manifest together."""
    chosen = name or default_name(url)
    result = RepoAddResult(
        name=chosen,
        url=url,
        track=None,
        ref=None,
        commit=None,
        describe=None,
        paths=(),
        dry_run=dry_run,
        managed=True,
    )
    if not url.strip() or url.startswith("-"):
        return replace(result, refusal=RepoRefusal("invalid-url", f"{url!r} is not a repository URL"))
    if not valid_name(chosen):
        return replace(result, refusal=RepoRefusal("invalid-name", f"{chosen!r} must match {NAME_PATTERN.pattern}"))
    directory = layout.bundle_dir / LANE_DIR / chosen
    if (layout.bundle_dir / page_path(chosen)).exists() or directory.exists():
        return replace(
            result, refusal=RepoRefusal("name-taken", f"{page_path(chosen)} or {LANE_DIR}/{chosen}/ already exists")
        )
    if chosen in _declared_names(layout):
        return replace(
            result,
            refusal=RepoRefusal("repo-declared", f"{layout.manifest_path}: repositories.{chosen} is already declared"),
        )
    git = _runner(layout, environ)
    if isinstance(git, RepoRefusal):
        return replace(result, refusal=git)
    branch = track or default_branch(git, url, cwd=layout.bundle_dir)
    if isinstance(branch, GitFailure):
        return replace(
            result, refusal=_git_refusal(branch, not_found="remote-unreachable", otherwise="remote-unreachable")
        )
    commit = resolve_ref(git, url, f"refs/heads/{branch}", cwd=layout.bundle_dir)
    if isinstance(commit, GitFailure):
        return replace(
            result,
            track=branch,
            ref=branch,
            refusal=_git_refusal(commit, not_found="ref-not-found", otherwise="remote-unreachable"),
        )
    if checkout is None:
        target = layout.worktrees_dir / chosen / branch
    elif checkout.is_absolute():
        target = checkout
    else:
        target = layout.root / checkout
    target = target.resolve()
    shown = _relative_to_root(layout, target)
    result = replace(
        result,
        track=branch,
        ref=branch,
        commit=commit,
        checkout=shown,
        paths=(page_path(chosen), f"{LANE_DIR}/index.md", "log.md"),
    )
    if target.exists() or target.is_symlink():
        return replace(
            result,
            refusal=RepoRefusal("checkout-exists", f"{shown} already exists; choose another --checkout or remove it"),
        )
    if dry_run:
        return result
    try:
        with locked(_operation_lock_path(layout, chosen)):
            return _add_managed_locked(layout, git, result, branch=branch, target=target, now=now, nonce=nonce)
    except OSError as exc:
        return replace(result, refusal=RepoRefusal("write-failed", str(exc)))


def _add_managed_locked(
    layout: WorkspaceLayout,
    git: Git,
    result: RepoAddResult,
    *,
    branch: str,
    target: Path,
    now: datetime,
    nonce: str | None,
) -> RepoAddResult:
    """The operation lock owns this clone until publication or rollback finishes."""
    chosen = result.name
    directory = layout.bundle_dir / LANE_DIR / chosen
    page = layout.bundle_dir / page_path(chosen)
    if page.exists() or directory.exists():
        return replace(result, refusal=RepoRefusal("name-taken", f"repository {chosen!r} was added meanwhile"))
    if chosen in _declared_names(layout):
        return replace(result, refusal=RepoRefusal("repo-declared", f"repositories.{chosen} was declared meanwhile"))
    if target.exists() or target.is_symlink():
        return replace(result, refusal=RepoRefusal("checkout-exists", f"{result.checkout} already exists"))
    assert result.commit is not None
    assert result.checkout is not None
    commit = result.commit
    shown = result.checkout
    url = result.url
    clone = layout.bundle_dir / clone_path(chosen)
    incoming = layout.cache_dir / "repo-incoming" / f"{chosen}-{nonce or secrets.token_hex(4)}"
    failure = materialize(git, url, clone, commit, incoming=incoming)
    if failure is not None:
        # `materialize` cleans only paths it reserved. A concurrently created destination is foreign.
        return replace(result, refusal=_git_refusal(failure, not_found="ref-not-found"))
    facts = commit_facts(git, clone, commit)
    if isinstance(facts, GitFailure):
        remove_tree(directory)
        return replace(result, refusal=_git_refusal(facts, not_found="git-failed"))
    linked = worktree_add(git, clone, target, branch)
    if linked is not None:
        # Registration against our newly materialized clone establishes ownership. When Git cannot
        # establish that link, preserve the checkout and clone rather than deleting a foreign path.
        listed = worktree_list(git, clone)
        if not isinstance(listed, GitFailure):
            registered = any(Path(tree.path).resolve() == target for tree in listed)
            if not registered or worktree_remove(git, clone, target) is None:
                remove_tree(directory)
        return replace(result, refusal=_git_refusal(linked, not_found="git-failed"))
    described = describe(git, clone, commit)
    pin = Pin(
        commit=commit,
        fetched_at=_instant(now),
        ref=branch,
        describe=described,
        tree=facts.tree,
        commit_date=facts.commit_date,
    )
    log = WriteLog(layout.bundle_dir)
    try:
        with held_bundle_lock(layout):
            if page.exists():
                refusal = RepoRefusal("name-taken", f"repository {chosen!r} was added meanwhile")
            elif chosen in _declared_names(layout):
                refusal = RepoRefusal("repo-declared", f"repositories.{chosen} was declared meanwhile")
            else:
                refusal = None
                manifest_before: bytes | None = None
                index_before: tuple[Path, bytes | None] | None = None
                try:
                    manifest_before = layout.manifest_path.read_bytes()
                    log.write(page_path(chosen), managed_page_text(name=chosen, url=url, track=branch, pin=pin))
                    set_value(layout.manifest_path, f"repositories.{chosen}.path", _relative_to_root(layout, clone))
                    set_value(layout.manifest_path, f"repositories.{chosen}.checkout", shown)
                    _reconcile_indexes(layout, log, chosen, directories=(LANE_DIR,))
                    label = described or commit[:7]
                    log_warning = _append_log(
                        layout,
                        log,
                        _entry("repo-add", chosen, f"{url} @ {label} (managed; checkout {shown})"),
                        today=now.astimezone(UTC).date(),
                    )
                    commit_root, _ = commit_target(layout)
                    if commit_root is not None:
                        index_name = run_git(commit_root, "rev-parse", "--path-format=absolute", "--git-path", "index")
                        if index_name is None:
                            raise OSError("cannot locate workspace Git index for rollback")
                        index = Path(index_name.strip())
                        index_before = (index, index.read_bytes() if index.exists() else None)
                    outcome, commit_warnings = _commit(
                        layout,
                        f"workspace: add managed repository {chosen}",
                        log,
                        root_paths=(layout.manifest_path.relative_to(layout.root).as_posix(),),
                    )
                    if outcome.status == "failed":
                        raise ValueError(f"workspace commit failed: {outcome.reason}")
                except (OSError, ValueError, StoreValidationError):
                    try:
                        log.rollback()
                    finally:
                        if manifest_before is not None:
                            layout.manifest_path.write_bytes(manifest_before)
                        if index_before is not None:
                            index, contents = index_before
                            if contents is None:
                                index.unlink(missing_ok=True)
                            else:
                                index.write_bytes(contents)
                    raise
    except (OSError, ValueError, StoreValidationError) as exc:
        worktree_remove(git, clone, target)
        remove_tree(directory)
        return replace(result, refusal=RepoRefusal("write-failed", str(exc)))
    if refusal is not None:
        worktree_remove(git, clone, target)
        remove_tree(directory)
        return replace(result, refusal=refusal)
    warnings = (*((log_warning,) if log_warning else ()), *commit_warnings)
    return replace(result, describe=described, paths=log.paths, commit_outcome=outcome, warnings=warnings)


RestoreKind = Literal["present", "redetached", "cloned", "refused"]


@dataclass(frozen=True, slots=True)
class RestoreOutcome:
    name: str
    outcome: RestoreKind
    commit: str | None
    refusal: RepoRefusal | None = None
    checkout: Literal["checkout-present", "checkout-created", "refused"] | None = None


@dataclass(frozen=True, slots=True)
class RepoRestoreResult:
    outcomes: tuple[RestoreOutcome, ...]
    dry_run: bool
    refusal: RepoRefusal | None = None

    @property
    def ok(self) -> bool:
        return self.refusal is None and all(outcome.refusal is None for outcome in self.outcomes)


def _lane_pages(bundle: Bundle) -> dict[str, Document]:
    return lane_pages(bundle)


def _restore_one(
    layout: WorkspaceLayout, git: Git, name: str, page: Document, *, dry_run: bool, nonce: str
) -> RestoreOutcome:
    try:
        with locked(_operation_lock_path(layout, name)):
            fresh_page = _lane_pages(load_workspace_bundle(layout)).get(name)
            if fresh_page is None:
                return RestoreOutcome(name, "refused", None, RepoRefusal("not-found", "repository page disappeared"))
            managed = fresh_page.fm_data(dates="iso").get("type") == MANAGED_TYPE
            if managed:
                # Predict clone status for the result, but refuse checkout problems before any mutation.
                predicted = _restore_clone(layout, git, name, fresh_page, dry_run=True, nonce=nonce)
                if predicted.refusal is not None:
                    return predicted
                checked = _restore_checkout(layout, git, name, fresh_page, dry_run=True)
                if checked.refusal is not None:
                    return replace(predicted, checkout="refused", refusal=checked.refusal)
            outcome = _restore_clone(layout, git, name, fresh_page, dry_run=dry_run, nonce=nonce)
            if outcome.refusal is not None or fresh_page.fm_data(dates="iso").get("type") != MANAGED_TYPE:
                return outcome
            step = _restore_checkout(layout, git, name, fresh_page, dry_run=dry_run)
            if step.refusal is not None:
                return replace(outcome, checkout="refused", refusal=step.refusal)
            return replace(outcome, checkout=step.checkout)
    except OSError as exc:
        return RestoreOutcome(name, "refused", None, RepoRefusal("git-failed", str(exc)))


def _restore_clone(
    layout: WorkspaceLayout, git: Git, name: str, page: Document, *, dry_run: bool, nonce: str
) -> RestoreOutcome:
    pin = read_pin(page)
    url = page.fm_data(dates="iso").get("url")
    url = url if isinstance(url, str) else ""
    clone = layout.bundle_dir / clone_path(name)
    exists = clone.is_dir()
    origin: str | None = None
    head: HeadState | None = None
    clean: bool | None = None
    if exists and pin is not None:
        probed_origin = origin_url(git, clone)
        probed_head = head_state(git, clone)
        probed_clean = is_clean(git, clone)
        for probed in (probed_origin, probed_head, probed_clean):
            if isinstance(probed, GitFailure):
                return RestoreOutcome(name, "refused", pin.commit, _git_refusal(probed, not_found="git-failed"))
        origin = probed_origin if isinstance(probed_origin, str) else None
        head = probed_head if isinstance(probed_head, HeadState) else None
        clean = probed_clean if isinstance(probed_clean, bool) else None
    decision = restore_action(
        pin_commit=pin.commit if pin else None, clone_exists=exists, origin=origin, url=url, head=head, clean=clean
    )
    commit = pin.commit if pin else None
    if decision in ("no-pin", "url-mismatch", "clone-dirty"):
        details = {
            "no-pin": f"{page_path(name)} has no usable pin.commit",
            "url-mismatch": f"the clone's origin is {origin!r}, the page's url is {url!r}",
            "clone-dirty": "the clone has tracked changes or untracked files; restore never discards them",
        }
        return RestoreOutcome(name, "refused", commit, RepoRefusal(decision, details[decision]))
    assert pin is not None  # every remaining decision needs a pin
    if decision == "present":
        return RestoreOutcome(name, "present", commit)
    if decision == "clone":
        if not dry_run:
            incoming = layout.cache_dir / "repo-incoming" / f"{name}-{nonce}"
            failure = materialize(git, url, clone, pin.commit, incoming=incoming)
            if failure is not None:
                return RestoreOutcome(name, "refused", commit, _git_refusal(failure, not_found="pin-unreachable"))
        return RestoreOutcome(name, "cloned", commit)
    if not dry_run:
        if not has_commit(git, clone, pin.commit):
            fetched = fetch(git, clone, pin.commit)
            if isinstance(fetched, GitFailure):
                return RestoreOutcome(name, "refused", commit, _git_refusal(fetched, not_found="pin-unreachable"))
        failure = detach(git, clone, pin.commit)
        if failure is not None:
            return RestoreOutcome(name, "refused", commit, _git_refusal(failure, not_found="pin-unreachable"))
    return RestoreOutcome(name, "redetached", commit)


def _restore_checkout(layout: WorkspaceLayout, git: Git, name: str, page: Document, *, dry_run: bool) -> RestoreOutcome:
    """Restore a managed checkout without touching an existing path."""
    target = _managed_checkout(layout, name)
    if target is None:
        return RestoreOutcome(
            name,
            "refused",
            None,
            RepoRefusal(
                "no-checkout",
                f"{layout.manifest_path}: no repositories.{name} entry with a checkout for {clone_path(name)}",
            ),
        )
    clone = layout.bundle_dir / clone_path(name)
    track = page.fm_data(dates="iso").get("track")
    if not isinstance(track, str) or not track:
        return RestoreOutcome(name, "refused", None, RepoRefusal("no-track", f"{page_path(name)} has no track"))
    occupied = target.exists() or target.is_symlink()
    linked = False
    registered = False
    if clone.is_dir():
        listed = worktree_list(git, clone)
        if isinstance(listed, GitFailure):
            return RestoreOutcome(name, "refused", None, _git_refusal(listed, not_found="git-failed"))
        registered = any(Path(entry.path).resolve() == target.resolve() for entry in listed)
        if occupied and target.is_dir() and not target.is_symlink() and registered:
            clone_common = common_dir(git, clone)
            if isinstance(clone_common, GitFailure):
                return RestoreOutcome(name, "refused", None, _git_refusal(clone_common, not_found="git-failed"))
            root = worktree_root(git, target)
            target_common = common_dir(git, target)
            branch = current_branch(git, target)
            for probed in (root, target_common, branch):
                if isinstance(probed, GitFailure):
                    return RestoreOutcome(name, "refused", None, _git_refusal(probed, not_found="git-failed"))
            linked = root == target.resolve() and target_common == clone_common and branch == track
    decision = checkout_action(exists=occupied, linked=linked)
    if decision == "checkout-foreign":
        detail = (
            f"{_relative_to_root(layout, target)} exists but is not a worktree of {clone_path(name)}; "
            f"move it aside, or run `git worktree repair {target}` from the clone if it was one"
        )
        return RestoreOutcome(name, "refused", None, RepoRefusal("checkout-foreign", detail))
    if decision == "checkout-create" and not dry_run:
        failure = worktree_add(git, clone, target, track, force_stale=registered)
        if failure is not None:
            return RestoreOutcome(name, "refused", None, _git_refusal(failure, not_found="git-failed"))
    return RestoreOutcome(
        name, "present", None, None, "checkout-created" if decision == "checkout-create" else "checkout-present"
    )


def run_repo_restore(
    layout: WorkspaceLayout,
    names: Sequence[str] = (),
    *,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
    nonce: str | None = None,
) -> RepoRestoreResult:
    """§4.2: bring every lane page's clone (or the named ones) to its pin. Writes no page and makes no commit."""
    pages = _lane_pages(load_workspace_bundle(layout))
    selected = list(names) or sorted(pages)
    if not selected:
        return RepoRestoreResult((), dry_run)
    git = _runner(layout, environ)
    if isinstance(git, RepoRefusal):
        return RepoRestoreResult((), dry_run, git)
    token = nonce or secrets.token_hex(4)
    outcomes = tuple(
        _restore_one(layout, git, name, pages[name], dry_run=dry_run, nonce=token)
        if name in pages
        else RestoreOutcome(
            name,
            "refused",
            None,
            RepoRefusal("not-found", f"no ManagedRepository or ReferenceRepository page at {page_path(name)}"),
        )
        for name in selected
    )
    return RepoRestoreResult(outcomes, dry_run)


AdvanceKind = Literal["advanced", "up-to-date", "refused"]


@dataclass(frozen=True, slots=True)
class RepoAdvanceResult:
    name: str
    outcome: AdvanceKind
    previous: str | None
    commit: str | None
    describe: str | None
    range: RangeFacts | None
    flagged: tuple[FlaggedPage, ...]
    paths: tuple[str, ...]
    proposals: tuple[str, ...]
    skipped: tuple[str, ...]
    dry_run: bool
    refusal: RepoRefusal | None = None
    commit_outcome: CommitOutcome | None = None
    warnings: tuple[str, ...] = ()
    managed: bool = False
    commits: int | None = None

    @property
    def ok(self) -> bool:
        return self.refusal is None


def _refused(result: RepoAdvanceResult, code: RefusalCode, detail: str) -> RepoAdvanceResult:
    return replace(result, outcome="refused", refusal=RepoRefusal(code, detail))


def _file_proposals(
    layout: WorkspaceLayout, log: WriteLog | None, flagged: tuple[FlaggedPage, ...], snapshot: Snapshot, now: datetime
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """One proposal per flagged page, planned against a fresh load each time (a prior apply changed the
    bundle). `log=None` plans only."""
    written: list[str] = []
    skipped: list[str] = []
    for page in flagged:
        bundle = load_workspace_bundle(layout)
        plan = plan_flag_proposal(bundle, page, snapshot, by=ACTOR, at=now)
        if plan.refusals:
            skipped.extend(f"{page.page}: {refusal.kind} — {refusal.detail}" for refusal in plan.refusals)
            continue
        if plan.is_empty:
            continue
        if log is not None:
            for write in plan.writes:
                log.remember(write.member)
            applied = apply_proposal(bundle, plan)
            if not applied.ok:
                raise ValueError(f"proposal for {page.page} did not land: {applied.failed[0]}")
        written.append(plan.proposal)
    return tuple(written), tuple(skipped)


#: Runs a structural scan of the workspace and returns its error lines. Injected by the interface, because this
#: vertical may not import `graph_works_core.scan` (root `pyproject.toml`, verticals never import each other).
Rescan = Callable[[], Sequence[str]]


def _generation(layout: WorkspaceLayout, name: str) -> Generation:
    clone = (layout.bundle_dir / clone_path(name)).resolve()
    entry = next((repo for repo in load_workspace_config(layout).repos if repo.path.resolve() == clone), None)
    ignore = list(entry.ignore) if entry is not None else []
    return Generation(version("graph-works-core"), scan_config_hash(_relative_to_root(layout, clone), ignore))


def _advance_managed(
    layout: WorkspaceLayout,
    name: str,
    page: Document,
    *,
    to: str | None,
    now: datetime,
    dry_run: bool,
    environ: Mapping[str, str] | None,
    rescan: Rescan | None,
) -> RepoAdvanceResult:
    """D-002: detach the clone at the local `track` tip, rescan, re-pin with generation facts, commit.

    Every failure after the detach re-detaches the clone at the old pin and discards what the scan wrote, so the
    clone, the committed pin and `code-graph/` never disagree.
    """
    result = RepoAdvanceResult(name, "refused", None, None, None, None, (), (), (), (), dry_run, managed=True)
    data = page.fm_data(dates="iso")
    if to is not None:
        return _refused(
            result, "to-unsupported", "a managed repository always advances to the tip of its track (D-002)"
        )
    pin = read_pin(page)
    if pin is None:
        return _refused(result, "no-pin", f"{page_path(name)} has no usable pin.commit")
    result = replace(result, previous=pin.commit)
    track = data.get("track")
    if not isinstance(track, str) or not track:
        return _refused(result, "no-track", f"{page_path(name)} has no track")
    clone = layout.bundle_dir / clone_path(name)
    if not clone.is_dir():
        return _refused(result, "clone-missing", f"{clone_path(name)}/ is absent; run `gw repo restore {name}`")
    checkout = _managed_checkout(layout, name)
    if checkout is None:
        return _refused(
            result,
            "no-checkout",
            f"{layout.manifest_path}: no repositories.{name} entry with a checkout for {clone_path(name)}",
        )
    if not checkout.is_dir():
        return _refused(
            result, "checkout-missing", f"{_relative_to_root(layout, checkout)} is absent; run `gw repo restore {name}`"
        )
    git = _runner(layout, environ)
    if isinstance(git, RepoRefusal):
        return replace(result, refusal=git)
    url = data.get("url") if isinstance(data.get("url"), str) else ""
    origin = origin_url(git, clone)
    head = head_state(git, clone)
    clean = is_clean(git, clone)
    branch = current_branch(git, checkout)
    checkout_clean = is_clean(git, checkout)
    root = worktree_root(git, checkout)
    checkout_common = common_dir(git, checkout)
    clone_common = common_dir(git, clone)
    listed = worktree_list(git, clone)
    for probed in (origin, head, clean, branch, checkout_clean, root, checkout_common, clone_common, listed):
        if isinstance(probed, GitFailure):
            return replace(result, refusal=_git_refusal(probed, not_found="git-failed"))
    assert not isinstance(listed, GitFailure)
    if (
        checkout.is_symlink()
        or root != checkout.resolve()
        or checkout_common != clone_common
        or not any(Path(entry.path).resolve() == checkout.resolve() for entry in listed)
    ):
        return _refused(result, "checkout-foreign", f"{checkout} is not a worktree of {clone_path(name)}")
    if origin != url:
        return _refused(result, "url-mismatch", f"the clone's origin is {origin!r}, the page's url is {url!r}")
    if isinstance(head, HeadState) and not head.detached:
        return _refused(
            result, "clone-not-detached", f"{clone_path(name)} has a branch checked out; the clone must stay detached"
        )
    if clean is not True:
        return _refused(
            result, "clone-dirty", "the clone has tracked changes or untracked files; advance never discards them"
        )
    if branch != track:
        return _refused(
            result, "checkout-off-track", f"{_relative_to_root(layout, checkout)} is on {branch!r}, not {track!r}"
        )
    if checkout_clean is not True:
        return _refused(
            result,
            "checkout-dirty",
            f"{_relative_to_root(layout, checkout)} has uncommitted changes; commit or merge them to {track} first",
        )
    new = branch_tip(git, clone, track)
    if isinstance(new, GitFailure):
        return replace(result, refusal=_git_refusal(new, not_found="ref-not-found"))
    if new == pin.commit:
        return replace(result, outcome="up-to-date", commit=new, describe=pin.describe)
    counted = rev_count(git, clone, pin.commit, new)
    if isinstance(counted, GitFailure):
        return replace(result, refusal=_git_refusal(counted, not_found="git-failed"))
    count = counted
    result = replace(result, commit=new, commits=count, paths=(page_path(name), "log.md"))
    if dry_run:
        return replace(result, outcome="advanced")
    if rescan is None:
        raise ValueError("a managed advance needs rescan= (the interface supplies a structural scan)")
    with held_bundle_lock(layout), graph_checkpoint(layout.cache_dir) as restore_graph:
        baseline = bundle_changes(layout)
        if baseline is None:
            return _refused(
                result,
                "workspace-unversioned",
                f"{layout.bundle_dir} is not in a git work tree; advance rolls a failed scan back through git",
            )
        if baseline:
            shown = ", ".join(change.path for change in baseline[:5])
            return _refused(
                result,
                "workspace-dirty",
                f"the bundle has uncommitted changes ({shown}); commit them before advancing",
            )

        def undo(detail: str, code: RefusalCode) -> RepoAdvanceResult:
            failures: list[str] = []
            try:
                changes = bundle_changes(layout)
                if changes is None:
                    raise OSError("cannot inspect bundle changes for rollback")
                discard_bundle_changes(layout, changes)
            except OSError as exc:
                failures.append(str(exc))
            restored = detach(git, clone, pin.commit)
            if restored is not None:
                failures.append(f"cannot restore clone: {restored.detail}")
            try:
                restore_graph()
            except OSError as exc:
                failures.append(str(exc))
            if failures:
                detail += "; rollback incomplete: " + "; ".join(failures)
            return _refused(result, code, detail)

        failure = detach(git, clone, new)
        if failure is not None:
            return undo(f"{failure.command}: {failure.detail}", "git-failed")
        try:
            errors = tuple(rescan())
        except Exception as exc:
            errors = (str(exc),)
        if errors:
            return undo("; ".join(errors), "scan-failed")
        scanned_changes = bundle_changes(layout)
        if scanned_changes is None:
            return undo("cannot inspect structural scan output", "scan-failed")
        scanned = tuple(change.path for change in scanned_changes)
        meta = commit_facts(git, clone, new)
        if isinstance(meta, GitFailure):
            return undo(f"{meta.command}: {meta.detail}", "git-failed")
        described = describe(git, clone, new)
        log = WriteLog(layout.bundle_dir)
        try:
            document = load(layout.bundle_dir / page_path(name))
            write_pin(
                document,
                Pin(
                    commit=new,
                    fetched_at=_instant(now),
                    ref=track,
                    describe=described,
                    tree=meta.tree,
                    commit_date=meta.commit_date,
                    previous=pin.commit,
                    generation=_generation(layout, name),
                ),
            )
            log.write(page_path(name), document.serialize())
            detail = f"{pin.commit[:7]}..{new[:7]}, {count if count is not None else '?'} commit(s), rescanned"
            log_warning = _append_log(
                layout, log, _entry("repo-advance", name, detail), today=now.astimezone(UTC).date()
            )
            outcome, commit_warnings = _commit(
                layout, f"workspace: advance managed repository {name} to {new[:7]}", log, extra=scanned
            )
            if outcome.status != "committed":
                raise ValueError(f"workspace commit {outcome.status}: {outcome.reason or 'no changes'}")
        except (OSError, ValueError) as exc:
            log.rollback()
            return undo(str(exc), "write-failed")
        warnings = (*((log_warning,) if log_warning else ()), *commit_warnings)
        return replace(
            result,
            outcome="advanced",
            describe=described,
            paths=tuple(sorted({*log.paths, *scanned})),
            commit_outcome=outcome,
            warnings=warnings,
            refusal=None,
        )


def run_repo_advance(
    layout: WorkspaceLayout,
    name: str,
    *,
    to: str | None = None,
    now: datetime,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
    rescan: Rescan | None = None,
) -> RepoAdvanceResult:
    """Serialize a repository's fetch, HEAD changes, page writes and rollback.

    The repository operation lock precedes the bundle lock. Network calls hold only the
    operation lock, so independent repositories and other bundle writers can proceed.
    """
    _require_aware(now)
    try:
        with locked(_operation_lock_path(layout, name)):
            return _run_repo_advance_locked(
                layout, name, to=to, now=now, dry_run=dry_run, environ=environ, rescan=rescan
            )
    except OSError as exc:
        return RepoAdvanceResult(
            name,
            "refused",
            None,
            None,
            None,
            None,
            (),
            (),
            (),
            (),
            dry_run,
            refusal=RepoRefusal("git-failed", str(exc)),
        )


def _run_repo_advance_locked(
    layout: WorkspaceLayout,
    name: str,
    *,
    to: str | None = None,
    now: datetime,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
    rescan: Rescan | None = None,
) -> RepoAdvanceResult:
    """§4.3: fetch, compute the range, detach, then write snapshot, changelog, pin, proposals, indexes and
        log, and commit.

        A write failure rolls every page back and re-detaches the clone at the old pin, so the clone and the
    committed pin never disagree.
    """
    _require_aware(now)
    result = RepoAdvanceResult(name, "refused", None, None, None, None, (), (), (), (), dry_run)
    bundle = load_workspace_bundle(layout)
    page = _lane_pages(bundle).get(name)
    if page is None:
        return _refused(result, "not-found", f"no repository page at {page_path(name)}")
    data = page.fm_data(dates="iso")
    if data.get("type") == MANAGED_TYPE:
        return _advance_managed(layout, name, page, to=to, now=now, dry_run=dry_run, environ=environ, rescan=rescan)
    pin = read_pin(page)
    if pin is None:
        return _refused(result, "no-pin", f"{page_path(name)} has no usable pin.commit")
    result = replace(result, previous=pin.commit)
    clone = layout.bundle_dir / clone_path(name)
    if not clone.is_dir():
        return _refused(result, "clone-missing", f"{clone_path(name)}/ is absent; run `gw repo restore {name}`")
    git = _runner(layout, environ)
    if isinstance(git, RepoRefusal):
        return replace(result, refusal=git)
    url = data.get("url") if isinstance(data.get("url"), str) else ""
    origin = origin_url(git, clone)
    if isinstance(origin, GitFailure):
        return replace(result, refusal=_git_refusal(origin, not_found="git-failed"))
    if origin != url:
        return _refused(result, "url-mismatch", f"the clone's origin is {origin!r}, the page's url is {url!r}")
    clean = is_clean(git, clone)
    if isinstance(clean, GitFailure):
        return replace(result, refusal=_git_refusal(clean, not_found="git-failed"))
    if not clean:
        return _refused(
            result, "clone-dirty", "the clone has tracked changes or untracked files; advance never discards them"
        )
    track = data.get("track")
    target = to or (track if isinstance(track, str) and track else None) or default_branch(git, str(url), cwd=clone)
    if isinstance(target, GitFailure):
        return replace(
            result, refusal=_git_refusal(target, not_found="remote-unreachable", otherwise="remote-unreachable")
        )
    new = fetch(git, clone, target)
    if isinstance(new, GitFailure):
        return replace(result, refusal=_git_refusal(new, not_found="ref-not-found", otherwise="remote-unreachable"))
    if new == pin.commit:
        return replace(result, outcome="up-to-date", commit=new, describe=pin.describe)
    facts = range_facts(git, clone, pin.commit, new)
    if isinstance(facts, GitFailure):
        return replace(result, refusal=_git_refusal(facts, not_found="git-failed"))
    meta = commit_facts(git, clone, new)
    if isinstance(meta, GitFailure):
        return replace(result, refusal=_git_refusal(meta, not_found="git-failed"))
    described = describe(git, clone, new)
    flagged = flag_pages(bundle, build_link_graph(bundle), name, facts.changes)
    fetched_at = _instant(now)
    snapshot = Snapshot(
        name, new, fetched_at, described, meta.commit_date, previous=pin.commit, range=facts, flagged=flagged
    )
    planned_proposals, planned_skips = _file_proposals(layout, None, flagged, snapshot, now)
    planned = tuple(
        sorted(
            {
                page_path(name),
                snapshot.path,
                changelog_path(name),
                f"{LANE_DIR}/index.md",
                f"{LANE_DIR}/{name}/index.md",
                f"{LANE_DIR}/{name}/snapshots/index.md",
                "log.md",
                *planned_proposals,
            }
        )
    )
    result = replace(
        result,
        commit=new,
        describe=described,
        range=facts,
        flagged=flagged,
        paths=planned,
        proposals=planned_proposals,
        skipped=planned_skips,
    )
    if dry_run:
        return replace(result, outcome="advanced")

    failure = detach(git, clone, new)
    if failure is not None:
        detach(git, clone, pin.commit)
        return replace(result, refusal=_git_refusal(failure, not_found="git-failed"))
    log = WriteLog(layout.bundle_dir)
    try:
        with held_bundle_lock(layout):
            try:
                bundle = load_workspace_bundle(layout)
                document = _lane_pages(bundle).get(name)
                fresh_pin = read_pin(document) if document is not None else None
                if (
                    fresh_pin != pin
                    or document is None
                    or document.fm_data(dates="iso").get("type") != "ReferenceRepository"
                    or document.fm_data(dates="iso").get("url") != url
                ):
                    detach(git, clone, fresh_pin.commit if fresh_pin is not None else pin.commit)
                    return _refused(result, "git-failed", "repository pin changed during fetch; retry advance")
                flagged = flag_pages(bundle, build_link_graph(bundle), name, facts.changes)
                snapshot = replace(snapshot, flagged=flagged)
                result = replace(result, flagged=flagged)
                assert document is not None
                write_pin(
                    document,
                    Pin(
                        commit=new,
                        fetched_at=fetched_at,
                        ref=target,
                        describe=described,
                        tree=meta.tree,
                        commit_date=meta.commit_date,
                        previous=pin.commit,
                    ),
                )
                log.write(page_path(name), document.serialize())
                # A snapshot's identity is its pin and date; authored Doc impact stays intact.
                if not (layout.bundle_dir / snapshot.path).is_file():
                    log.write(snapshot.path, render_snapshot(snapshot))
                changelog_file = layout.bundle_dir / changelog_path(name)
                before = changelog_file.read_bytes().decode("utf-8") if changelog_file.is_file() else None
                log.write(
                    changelog_path(name),
                    reconcile_changelog(
                        before, name=name, entries=(*entries_from_bundle(bundle, name), entry_for(snapshot))
                    ),
                )
                written, skipped = _file_proposals(layout, log, flagged, snapshot, now)
                _reconcile_indexes(layout, log, name)
                detail = f"{pin.commit[:7]}..{new[:7]}, {summary(snapshot)}, {len(flagged)} page(s) flagged"
                log_warning = _append_log(
                    layout, log, _entry("repo-advance", name, detail), today=now.astimezone(UTC).date()
                )
                outcome, commit_warnings = _commit(
                    layout, f"workspace: advance reference repository {name} to {new[:7]}", log
                )
            except (OSError, ValueError):
                log.rollback()
                raise
    except (OSError, ValueError) as exc:
        detach(git, clone, pin.commit)
        return replace(result, refusal=RepoRefusal("write-failed", str(exc)))
    warnings = (*((log_warning,) if log_warning else ()), *commit_warnings)
    return replace(
        result,
        outcome="advanced",
        paths=log.paths,
        proposals=written,
        skipped=skipped,
        commit_outcome=outcome,
        warnings=warnings,
    )

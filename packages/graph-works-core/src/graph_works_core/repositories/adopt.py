"""`gw repo adopt <name>`: move a declared primary checkout into the lane as a managed repository.

This is `gw repo add --managed` for a clone that already exists (design §4.4): instead of partial-cloning, the
checkout itself is renamed to `okf/repositories/<name>/references/git`, detached at its HEAD (the pin), a
working checkout is linked on `track` at `.gw/worktrees/<name>/<track>`, and every existing linked worktree is
repaired to relative links. Page, manifest (`path` + `checkout`), lane index and log land in one workspace commit.

Every refusal happens before anything is touched. A failure after the first git step is rolled back to the old
layout: the checkout is renamed back, links repaired, the branch re-attached, a worktree adopt created removed.
`worktree.useRelativePaths` stays set after a rollback; git reads both link forms, so the old layout still works.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from repositories_okf.git import (
    Git,
    GitFailure,
    attach,
    commit_facts,
    current_branch,
    describe,
    detach,
    head_state,
    is_linked_worktree,
    is_pristine,
    origin_url,
    set_config,
    toplevel,
    worktree_add,
    worktree_list,
    worktree_remove,
    worktree_repair,
)
from repositories_okf.lane import LANE_DIR
from repositories_okf.lifecycle import clone_path, managed_page_text, page_path, scan_config_hash
from repositories_okf.pin import Generation, Pin

from graph_works_core.repositories.commands import (
    RefusalCode,
    RepoRefusal,
    _append_log,
    _commit,
    _entry,
    _git_refusal,
    _instant,
    _reconcile_indexes,
    _relative_to_root,
    _require_aware,
    _runner,
)
from graph_works_core.repositories.writes import WriteLog
from graph_works_core.workspace.commits import CommitOutcome
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import workspace_store
from graph_works_core.workspace.manifest_edit import repoint_repository
from graph_works_core.workspace.provenance import probe_git
from graph_works_core.workspace.transactions import held_bundle_lock

__all__ = ["RepoAdoptResult", "run_repo_adopt"]


@dataclass(frozen=True, slots=True)
class RepoAdoptResult:
    name: str
    source: str | None
    clone: str | None
    checkout: str | None
    track: str | None
    commit: str | None
    checkout_created: bool
    repaired: tuple[str, ...]
    paths: tuple[str, ...]
    dry_run: bool
    refusal: RepoRefusal | None = None
    commit_outcome: CommitOutcome | None = None
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.refusal is None


@dataclass(frozen=True, slots=True)
class _Plan:
    name: str
    declared: str  # the manifest's own spelling of the old path, for the log
    source: Path
    clone: Path
    checkout: Path
    track: str
    branch: str | None  # the branch to re-attach on rollback; None when HEAD was already detached
    head: str
    url: str
    linked: tuple[Path, ...]  # existing linked worktrees (prunable ones excluded)
    reuse_checkout: bool
    ignore: tuple[str, ...]
    warnings: tuple[str, ...]


class _Refused(Exception):
    def __init__(self, code: RefusalCode, detail: str) -> None:
        super().__init__(detail)
        self.refusal = RepoRefusal(code, detail)


def _ok[T](value: T | GitFailure, *, code: RefusalCode = "git-failed") -> T:
    if isinstance(value, GitFailure):
        refusal = _git_refusal(value, not_found=code)
        raise _Refused(refusal.code, refusal.detail)
    return value


def _same_device(a: Path, b: Path) -> bool:
    return a.stat().st_dev == b.stat().st_dev


def _ignored(layout: WorkspaceLayout, relative: str) -> bool | None:
    """Whether the workspace's git ignores *relative* (a directory, root-relative); `None` when it cannot say."""
    outcome = probe_git(layout.root, "check-ignore", "-q", f"{relative}/")
    return None if outcome.returncode is None else {0: True, 1: False}.get(outcome.returncode)


def _inside(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


def _preflight(layout: WorkspaceLayout, git: Git, name: str, track: str | None) -> _Plan:
    entry = next((repo for repo in load_workspace_config(layout).repos if repo.name == name), None)
    if entry is None:
        raise _Refused("unknown-repository", f"repositories.{name} is not declared in {layout.manifest_path}")
    overlay = workspace_store(layout).read_overlay_explicit().get("repositories")
    local = overlay.get(name) if isinstance(overlay, Mapping) else None
    if isinstance(local, Mapping) and ({"path", "checkout"} & set(local)):
        raise _Refused(
            "local-override", f"{layout.local_manifest_path} overrides repositories.{name}; remove the override first"
        )
    source = entry.path.resolve()
    bundle = layout.bundle_dir.resolve()
    if _inside(source, bundle):
        raise _Refused("already-in-bundle", f"{source} is already inside the bundle")
    if (
        not source.is_dir()
        or _ok(toplevel(git, source), code="not-a-primary-checkout") != source
        or _ok(is_linked_worktree(git, source))
    ):
        raise _Refused("not-a-primary-checkout", f"{source} is not the top level of a repository's primary checkout")
    clone = (layout.bundle_dir / clone_path(name)).resolve()
    if (layout.bundle_dir / page_path(name)).exists() or clone.exists():
        raise _Refused("lane-occupied", f"{page_path(name)} or {clone_path(name)} already exists")
    clone_rel = _relative_to_root(layout, clone)
    ignored = _ignored(layout, clone_rel)
    if ignored is None:
        raise _Refused(
            "workspace-unversioned", f"{layout.root} is not a git work tree; the clone could not be kept out of it"
        )
    if not ignored:
        raise _Refused("not-ignored", f"the workspace's .gitignore does not cover {clone_rel}/ (run gw bootstrap)")
    branch = _ok(current_branch(git, source))
    chosen = track or branch
    if chosen is None:
        raise _Refused("no-track", f"{source} is detached; pass --track <branch>")
    if track is not None and branch is not None and track != branch:
        raise _Refused("track-mismatch", f"{source} is on {branch}, not {track}")
    url = _ok(origin_url(git, source))
    if not url:
        raise _Refused("no-origin", f"{source} has no origin remote")
    if not _ok(is_pristine(git, source)):
        raise _Refused("checkout-dirty", f"{source} has tracked changes or untracked files")
    worktrees = _ok(worktree_list(git, source))
    linked, warnings = [], []
    for worktree in worktrees[1:]:
        path = Path(worktree.path)
        if not path.is_dir():
            warnings.append(f"skipped prunable worktree {path} (run git worktree prune)")
            continue
        if not _ok(is_pristine(git, path)):
            raise _Refused("worktree-dirty", f"linked worktree {path} has tracked changes or untracked files")
        linked.append(path)
    checkout = (layout.worktrees_dir / name / chosen).resolve()
    reuse = False
    if checkout.exists():
        match = next((w for w in worktrees[1:] if Path(w.path).resolve() == checkout), None)
        if match is None or match.branch != chosen:
            raise _Refused(
                "track-worktree-conflict", f"{checkout} exists and is not a worktree of {source} on {chosen}"
            )
        reuse = True
    elif any(w.branch == chosen for w in worktrees[1:]):
        holder = next(w.path for w in worktrees[1:] if w.branch == chosen)
        raise _Refused("track-checked-out", f"{chosen} is checked out in {holder}; switch it away first")
    if not _same_device(source, layout.bundle_dir):
        raise _Refused("cross-device", f"{source} and {layout.bundle_dir} are on different filesystems")
    if _inside(Path.cwd().resolve(), source):
        raise _Refused("cwd-inside-checkout", f"the current directory is inside {source}; run adopt from elsewhere")
    try:
        repoint_repository(
            layout.manifest_path.read_bytes().decode("utf-8"),
            name,
            path=clone_rel,
            checkout=_relative_to_root(layout, checkout),
        )
    except WorkspaceError as exc:
        raise _Refused("manifest-unsupported", str(exc)) from exc
    head = _ok(head_state(git, source)).commit
    if head is None:
        raise _Refused("git-failed", f"{source} has no commit")
    return _Plan(
        name=name,
        declared=_declared_spelling(layout, name, source),
        source=source,
        clone=clone,
        checkout=checkout,
        track=chosen,
        branch=branch,
        head=head,
        url=url,
        linked=tuple(linked),
        reuse_checkout=reuse,
        ignore=entry.ignore,
        warnings=tuple(warnings),
    )


def _declared_spelling(layout: WorkspaceLayout, name: str, source: Path) -> str:
    repositories = workspace_store(layout).read_base_explicit().get("repositories")
    entry = repositories.get(name) if isinstance(repositories, Mapping) else None
    raw = entry.get("path") if isinstance(entry, Mapping) else None
    return raw if isinstance(raw, str) else str(source)


def _planned_paths(layout: WorkspaceLayout, name: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                page_path(name),
                f"{LANE_DIR}/index.md",
                "log.md",
                layout.manifest_path.relative_to(layout.root).as_posix(),
            }
        )
    )


def _result(layout: WorkspaceLayout, plan: _Plan, *, dry_run: bool) -> RepoAdoptResult:
    return RepoAdoptResult(
        name=plan.name,
        source=_relative_to_root(layout, plan.source),
        clone=_relative_to_root(layout, plan.clone),
        checkout=_relative_to_root(layout, plan.checkout),
        track=plan.track,
        commit=plan.head,
        checkout_created=not plan.reuse_checkout,
        repaired=tuple(str(path) for path in plan.linked),
        paths=_planned_paths(layout, plan.name),
        dry_run=dry_run,
        warnings=plan.warnings,
    )


def run_repo_adopt(
    layout: WorkspaceLayout,
    name: str,
    *,
    track: str | None = None,
    now: datetime,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
) -> RepoAdoptResult:
    """Adopt `repositories.<name>`'s checkout into the lane (design §4). `dry_run` stops after the preflight."""
    _require_aware(now)
    empty = RepoAdoptResult(name, None, None, None, track, None, False, (), (), dry_run)
    git = _runner(layout, environ)
    if isinstance(git, RepoRefusal):
        return replace(empty, refusal=git)
    try:
        plan = _preflight(layout, git, name, track)
    except _Refused as refused:
        return replace(empty, refusal=refused.refusal)
    result = _result(layout, plan, dry_run=dry_run)
    if dry_run:
        return result
    return _adopt(layout, git, plan, result, now=now)


@dataclass
class _Progress:
    """What has been done, so a rollback undoes exactly that."""

    committed: bool = False
    detached: bool = False
    created: bool = False
    moved: bool = False
    repair: tuple[Path, ...] = ()

    def published(self) -> None:
        self.committed = True


def _adopt(
    layout: WorkspaceLayout, git: Git, plan: _Plan, result: RepoAdoptResult, *, now: datetime
) -> RepoAdoptResult:
    progress = _Progress()
    try:
        _ok(set_config(git, plan.source, "worktree.useRelativePaths", "true"))
        if plan.branch is not None:
            _ok(detach(git, plan.source, plan.head))
            progress.detached = True
        if not plan.reuse_checkout:
            plan.checkout.parent.mkdir(parents=True, exist_ok=True)
            _ok(worktree_add(git, plan.source, plan.checkout, plan.track))
            progress.created = True
        progress.repair = tuple(Path(w.path) for w in _ok(worktree_list(git, plan.source))[1:] if Path(w.path).is_dir())
        plan.clone.parent.mkdir(parents=True, exist_ok=True)
        _move(plan.source, plan.clone)
        progress.moved = True
        _ok(worktree_repair(git, plan.clone, progress.repair))
        paths, outcome, warnings = _record(layout, git, plan, now=now, progress=progress)
    except BaseException as exc:
        if progress.committed:
            raise
        if not isinstance(exc, (_Refused, OSError, ValueError, WorkspaceError)):
            problems = _rollback(layout, git, plan, progress)
            if problems:
                exc.add_note("rollback incomplete: " + "; ".join(problems))
            raise
        refusal = exc.refusal if isinstance(exc, _Refused) else RepoRefusal("write-failed", str(exc))
        problems = _rollback(layout, git, plan, progress)
        detail = refusal.detail + ("; rollback incomplete: " + "; ".join(problems) if problems else "")
        return replace(result, refusal=RepoRefusal(refusal.code, detail))
    return replace(
        result,
        paths=paths,
        commit_outcome=outcome,
        warnings=(*result.warnings, *warnings),
        repaired=tuple(str(p) for p in progress.repair),
    )


def _move(source: Path, target: Path) -> None:
    """A same-filesystem rename (checked in preflight). Never a copy: this is the user's only clone."""
    os.replace(source, target)  # noqa: PTH105 — adoption explicitly uses an atomic rename.


def _record(
    layout: WorkspaceLayout, git: Git, plan: _Plan, *, now: datetime, progress: _Progress
) -> tuple[tuple[str, ...], CommitOutcome, tuple[str, ...]]:
    """Design §4.2 step 7: page, manifest, lane index, log, one commit, under the bundle lock."""
    facts = _ok(commit_facts(git, plan.clone, plan.head))
    described = describe(git, plan.clone, plan.head)
    clone_rel = _relative_to_root(layout, plan.clone)
    checkout_rel = _relative_to_root(layout, plan.checkout)
    pin = Pin(
        commit=plan.head,
        fetched_at=_instant(now),
        describe=described,
        tree=facts.tree,
        commit_date=facts.commit_date,
        generation=Generation(
            gw_version=version("graph-works-core"), scan_config_hash=scan_config_hash(clone_rel, plan.ignore)
        ),
    )
    manifest_rel = layout.manifest_path.relative_to(layout.root).as_posix()
    log = WriteLog(layout.bundle_dir)
    with held_bundle_lock(layout):
        manifest_before = layout.manifest_path.read_bytes()
        index_paths = (
            *(
                (layout.bundle_dir / member).relative_to(layout.root).as_posix()
                for member in (page_path(plan.name), f"{LANE_DIR}/index.md", "log.md")
            ),
            manifest_rel,
        )
        index_before = _index_snapshot(layout, index_paths)
        try:
            log.write(page_path(plan.name), managed_page_text(name=plan.name, url=plan.url, track=plan.track, pin=pin))
            spliced = repoint_repository(
                layout.manifest_path.read_bytes().decode("utf-8"), plan.name, path=clone_rel, checkout=checkout_rel
            )
            layout.manifest_path.write_text(spliced, encoding="utf-8", newline="")
            _reconcile_indexes(layout, log, plan.name, directories=(LANE_DIR,))
            label = described or plan.head[:7]
            detail = f"{plan.declared} -> {clone_rel} @ {label} (managed; checkout {checkout_rel})"
            log_warning = _append_log(
                layout, log, _entry("repo-adopt", plan.name, detail), today=now.astimezone(UTC).date()
            )
            outcome, commit_warnings = _commit(
                layout,
                f"workspace: adopt managed repository {plan.name}",
                log,
                root_paths=(manifest_rel,),
                on_committed=progress.published,
            )
            progress.committed = outcome.status == "committed"
            if outcome.status == "failed":
                raise _Refused("write-failed", f"workspace commit failed: {outcome.reason}")
        except BaseException:
            # Once the commit succeeded, the adoption is published and cannot be rolled back.
            if not progress.committed:
                log.rollback()
                layout.manifest_path.write_bytes(manifest_before)
                _restore_index(layout, index_paths, index_before)
            raise
    return (*log.paths, manifest_rel), outcome, (*((log_warning,) if log_warning else ()), *commit_warnings)


def _index_snapshot(layout: WorkspaceLayout, paths: tuple[str, ...]) -> str:
    outcome = probe_git(layout.root, "--literal-pathspecs", "ls-files", "--stage", "-z", "--", *paths)
    if outcome.returncode != 0:
        raise OSError(f"could not snapshot workspace index: {outcome.stderr or outcome.cause}")
    return outcome.stdout


def _restore_index(layout: WorkspaceLayout, paths: tuple[str, ...], before: str) -> None:
    # Remove new entries first, then restore every original stage (including staged deletions/conflicts).
    removals = "".join(f"0 {'0' * 40}\t{path}\0" for path in paths)
    outcome = probe_git(layout.root, "update-index", "-z", "--index-info", input_text=removals + before)
    if outcome.returncode != 0:
        raise OSError(f"could not restore workspace index: {outcome.stderr or outcome.cause}")


def _rollback(layout: WorkspaceLayout, git: Git, plan: _Plan, progress: _Progress) -> list[str]:
    """Undo *progress* in reverse. Every step is attempted; what failed is returned for the refusal detail."""
    problems: list[str] = []
    if progress.moved:
        try:
            _move(plan.clone, plan.source)
        except OSError as exc:
            problems.append(f"could not move {plan.clone} back to {plan.source}: {exc}")
        else:
            if isinstance(failure := worktree_repair(git, plan.source, progress.repair), GitFailure):
                problems.append(f"worktree repair: {failure.detail}")
    home = plan.source if plan.source.is_dir() else plan.clone
    if progress.created and isinstance(failure := worktree_remove(git, home, plan.checkout), GitFailure):
        problems.append(f"could not remove {plan.checkout}: {failure.detail}")
    if (
        progress.detached
        and plan.branch is not None
        and isinstance(failure := attach(git, home, plan.branch), GitFailure)
    ):
        problems.append(f"could not re-attach {plan.branch}: {failure.detail}")
    for directory in (plan.clone.parent, plan.clone.parent.parent):  # references/, then repositories/<name>/
        with suppress(OSError):
            directory.rmdir()
    return problems

"""Structural scan of one repository through plan/apply (D-001, D-006): no model, no other repository touched.

The plan is `sync_bundle`'s dry run scoped to one repository; applying re-plans
under the bundle root lock, hands that candidate to `before_apply`, runs the wet
sync with the same scope, and commits exactly the bundle members it changed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import date, datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal

from code_graph_io import GraphReader
from code_wiki_okf.config import Config
from code_wiki_okf.sync import SyncResult, sync_bundle

from graph_works_core.graph import commands as graph
from graph_works_core.scan.commands import StructuralSummary, open_scan_reader
from graph_works_core.workspace.bundle import CLONE_IGNORE, CLONE_PRUNE
from graph_works_core.workspace.commits import CommitOutcome, WorkspaceCommit
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.transactions import commit_pending, held_bundle_lock

RepoScanRefusal = Literal["unknown-repository", "graph-build-failed"]

#: One file's identity for change detection: a rewrite moves at least one of these.
_Stat = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class RepoScanRun:
    """One repository's structural scan: the planned (or applied) summary, or why it was refused.

    `applied` and `commit` stay empty on a plan and on a refusal.
    """

    repo: str
    structural: StructuralSummary
    refusal: RepoScanRefusal | None
    detail: str | None
    applied: bool = False
    commit: CommitOutcome | None = None


def _sync(
    layout: WorkspaceLayout, config: Config, reader: GraphReader, repo: str, at: datetime, today: date, *, dry_run: bool
) -> SyncResult:
    return sync_bundle(
        layout.bundle_dir,
        config=config,
        reader=reader,
        at=at.isoformat(),
        today=today,
        dry_run=dry_run,
        ignore=CLONE_IGNORE,
        prune=CLONE_PRUNE,
        repos=frozenset({repo}),
    )


def _bundle_state(bundle_dir: Path) -> dict[str, _Stat]:
    """Every bundle file's stat identity, clones pruned, keyed by bundle-relative posix path.

    `SyncResult` does not name every member a wet sync touches -- a mirror move
    rewrites its referrers and prunes emptied directories without reporting
    either -- so the commit pathspec is the before/after difference, taken under
    the bundle lock that excludes every other gw writer.
    """
    state: dict[str, _Stat] = {}
    for current, dirs, files in os.walk(bundle_dir):
        base = Path(current)
        relative_dir = base.relative_to(bundle_dir).as_posix()
        dirs[:] = [
            name
            for name in dirs
            if name != ".git"
            and not any(
                fnmatchcase(name if relative_dir == "." else f"{relative_dir}/{name}", pattern)
                for pattern in CLONE_PRUNE
            )
        ]
        for name in files:
            stat = (base / name).lstat()
            member = name if relative_dir == "." else f"{relative_dir}/{name}"
            state[member] = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    return state


def _changed(before: dict[str, _Stat], after: dict[str, _Stat]) -> tuple[str, ...]:
    """Members created, rewritten or removed between two `_bundle_state` snapshots."""
    return tuple(sorted(member for member in before.keys() | after.keys() if before.get(member) != after.get(member)))


def _refused(layout: WorkspaceLayout, config: Config, repo: str, target: graph.GraphTarget) -> RepoScanRun | None:
    """Refuse an undeclared repository before building; otherwise rebuild its graph alone."""
    if repo not in {declared.name for declared in config.repos}:
        return RepoScanRun(repo, StructuralSummary(), "unknown-repository", f"{repo!r} is not a declared repository")
    built = graph.build(target, only=repo)
    if not built.ok:
        return RepoScanRun(repo, StructuralSummary(), "graph-build-failed", built.error or "code graph build failed")
    return None


def run_repo_scan(
    layout: WorkspaceLayout,
    config: Config,
    *,
    repo: str,
    at: datetime,
    today: date,
    dry_run: bool = True,
    before_apply: Callable[[RepoScanRun], None] | None = None,
) -> RepoScanRun:
    """Plan (default) or apply the structural scan of declared repository *repo*.

    Only *repo*'s graph is rebuilt and only its pages, catalogs and indexes are
    synced; generated `at:` stamps are *at*, so a plan is stable at a pinned
    instant. Refusals are results, never raised.

    The graph build runs before the bundle lock: it writes only the cache,
    which the bundle lock does not guard. Applying then holds the bundle root
    lock across the plan, `before_apply`, the wet sync and the commit.
    `before_apply` sees the candidate -- refusals included -- before any bundle
    write; raising aborts the call. Dry runs never invoke it. The commit is
    exactly the members the sync changed, under `workflow.workspace_commits`,
    subject `workspace: scan <repo>`.

    Raises `ScanError` when the rebuilt graph cannot be opened.
    """
    target = graph.graph_target(layout)
    refusal = _refused(layout, config, repo, target)
    if refusal is not None:
        if not dry_run and before_apply is not None:
            before_apply(refusal)
        return refusal
    reader = open_scan_reader(target)
    try:
        with nullcontext() if dry_run else held_bundle_lock(layout):
            planned = _sync(layout, config, reader, repo, at, today, dry_run=True)
            run = RepoScanRun(repo, StructuralSummary.from_sync_result(planned), None, None)
            if dry_run:
                return run
            if before_apply is not None:
                before_apply(run)
            before = _bundle_state(layout.bundle_dir)
            result = _sync(layout, config, reader, repo, at, today, dry_run=False)
            written = _changed(before, _bundle_state(layout.bundle_dir))
            subject = f"workspace: scan {repo}"
            commit = commit_pending(layout, WorkspaceCommit(subject, extra_paths=written)) if written else None
    finally:
        reader.close()
    return replace(run, structural=StructuralSummary.from_sync_result(result), applied=True, commit=commit)


__all__ = ["RepoScanRefusal", "RepoScanRun", "run_repo_scan"]

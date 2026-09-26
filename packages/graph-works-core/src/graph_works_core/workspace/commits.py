"""One pathspec-limited workspace commit per gw transition.

A gw verb that changes pipeline state commits that change itself, exactly
once, under the lock that serialized the write (`transactions.apply_mutation`
calls `commit_workspace` inside `_bundle_root_lock`). Staging is always by
pathspec -- the plan's own members plus each named item's `references/` -- so a
sibling's in-flight file or a human's staged change is never swept in, and
`git commit --only` leaves everything outside the pathspec exactly as staged.

Failures are *reported*, never raised: the mutation is already correct on
disk, and a missing commit is recoverable by the item's next verb. Only an
invalid `workflow.workspace_commits` value raises -- a configuration error --
and `apply_mutation` resolves the mode before its first effect so that raise
can never follow a write.

git runs through `provenance.probe_git`, the package's one subprocess helper.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from work_tracker_okf.mutation import WorkMutationPlan
from work_tracker_okf.paths import references_dir

from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import checked_str
from graph_works_core.workspace.provenance import GitOutcome, probe_git

CommitMode = Literal["auto", "on", "off"]
COMMIT_MODES: tuple[CommitMode, ...] = ("auto", "on", "off")
MODE_KEY = "workflow.workspace_commits"
SUBJECT_PREFIX = "workspace: "
COMMIT_FAILED_PREFIX = "workspace commit failed: "
#: Skip reasons worth one `[note]`: the user may have expected a commit.
NOTE_REASONS: frozenset[str] = frozenset({"not-own-repo", "not-a-repo", "git missing"})
COMMIT_TIMEOUT_SECONDS = 30.0
_INDEX_LOCK_DELAYS: tuple[float, ...] = (0.2, 0.5, 1.0)
#: Test seam: `monkeypatch.setattr(commits, "_sleep", ...)`.
_sleep = time.sleep


@dataclass(frozen=True, slots=True)
class WorkspaceCommit:
    """What a verb asks `apply_mutation` to commit after a successful apply."""

    subject: str
    items: tuple[str, ...] = ()
    extra_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if "\n" in self.subject or "\r" in self.subject:
            raise ValueError(f"workspace commit subject must be one line: {self.subject!r}")
        if not self.subject.startswith(SUBJECT_PREFIX) or not self.subject[len(SUBJECT_PREFIX) :].strip():
            raise ValueError(f"workspace commit subject must start {SUBJECT_PREFIX!r}: {self.subject!r}")


@dataclass(frozen=True, slots=True)
class CommitOutcome:
    """What `commit_workspace` did. `paths` are toplevel-relative pathspec entries."""

    status: Literal["committed", "skipped", "failed"]
    sha: str | None
    subject: str
    paths: tuple[str, ...]
    reason: str | None


def item_stem(path: str) -> str:
    """The canonical work path's last segment -- the name a subject uses."""
    return path.rstrip("/").rsplit("/", 1)[-1]


def commit_mode(layout: WorkspaceLayout) -> CommitMode:
    """`workflow.workspace_commits`, validated against its enum."""
    value = checked_str(layout, MODE_KEY)
    if value not in COMMIT_MODES:
        raise WorkspaceError(
            f"{layout.manifest_path}: {MODE_KEY}: expects one of {', '.join(COMMIT_MODES)}, got {value!r}"
        )
    return value


def commit_target(layout: WorkspaceLayout, mode: CommitMode | None = None) -> tuple[Path | None, str | None]:
    """`(toplevel, None)` to commit there, or `(None, reason)` to skip."""
    resolved_mode = commit_mode(layout) if mode is None else mode
    if resolved_mode == "off":
        return None, "disabled"
    probe = probe_git(layout.root, "rev-parse", "--show-toplevel")
    if probe.returncode is None:
        return None, _reason(probe)
    if probe.returncode != 0:
        if "not a git repository" in probe.stderr.lower():
            return None, "not-a-repo"
        return None, _reason(probe)
    if not probe.stdout.strip():
        return None, "git rev-parse returned no toplevel"
    toplevel = Path(probe.stdout.strip()).resolve()
    if resolved_mode == "auto" and toplevel != layout.root.resolve():
        return None, "not-own-repo"
    return toplevel, None


def plan_paths(plan: WorkMutationPlan) -> tuple[str, ...]:
    """Every bundle-relative member *plan* writes, moves (both ends) or deletes."""
    members = {write.member for write in plan.writes}
    members.update(move.source for move in plan.moves)
    members.update(move.dest for move in plan.moves)
    members.update(plan.deletes)
    return tuple(sorted(members))


def _reason(outcome: GitOutcome) -> str:
    if outcome.returncode is None:
        return f"git {outcome.cause}"
    lines = [line.strip() for line in (outcome.stderr or outcome.stdout).splitlines() if line.strip()]
    return lines[0] if lines else f"git exited {outcome.returncode}"


def _git(toplevel: Path, *args: str) -> GitOutcome:
    """`git --literal-pathspecs <args>`, retrying while another git holds `index.lock`."""
    outcome = probe_git(toplevel, "--literal-pathspecs", *args, timeout=COMMIT_TIMEOUT_SECONDS)
    for delay in _INDEX_LOCK_DELAYS:
        if outcome.returncode in (0, None) or "index.lock" not in outcome.stderr:
            return outcome
        _sleep(delay)
        outcome = probe_git(toplevel, "--literal-pathspecs", *args, timeout=COMMIT_TIMEOUT_SECONDS)
    return outcome


def _pathspec(toplevel: Path, candidates: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    """Requested paths to commit and paths safe to pass to `git add`."""
    tracked = _git(toplevel, "ls-files", "-z", "--", *candidates)
    if tracked.returncode != 0:
        return (), (), _reason(tracked)
    # An unborn branch has a symbolic HEAD but no branch ref. Skip ls-tree
    # only in that state; any actual ls-tree failure must reach the caller.
    head_ref = _git(toplevel, "symbolic-ref", "-q", "HEAD")
    unborn = False
    if head_ref.returncode == 0 and head_ref.stdout.strip():
        ref = _git(toplevel, "show-ref", "--verify", "--quiet", head_ref.stdout.strip())
        unborn = ref.returncode == 1
    if unborn:
        head: list[str] = []
    else:
        in_head = _git(toplevel, "ls-tree", "-r", "--name-only", "-z", "HEAD", "--", *candidates)
        if in_head.returncode != 0:
            return (), (), _reason(in_head)
        head = [entry for entry in in_head.stdout.split("\0") if entry]
    known = [entry for entry in tracked.stdout.split("\0") if entry]
    add_paths = tuple(
        candidate
        for candidate in candidates
        if (toplevel / candidate).exists() or any(k == candidate or k.startswith(f"{candidate}/") for k in known)
    )
    commit_paths = tuple(
        candidate
        for candidate in candidates
        if candidate in add_paths or any(k == candidate or k.startswith(f"{candidate}/") for k in head)
    )
    return commit_paths, add_paths, None


def commit_workspace(
    layout: WorkspaceLayout,
    commit: WorkspaceCommit,
    plan_paths: Sequence[str],
    *,
    mode: CommitMode | None = None,
) -> CommitOutcome:
    """Stage and commit exactly *plan_paths* + `commit.extra_paths` + each item's `references/`."""
    toplevel, skip = commit_target(layout, mode)
    if toplevel is None:
        status: Literal["skipped", "failed"] = "skipped" if skip in {"disabled", *NOTE_REASONS} else "failed"
        return CommitOutcome(status, None, commit.subject, (), skip)
    bundle = layout.bundle_dir.resolve()
    if not bundle.is_relative_to(toplevel):
        return CommitOutcome("failed", None, commit.subject, (), "bundle is outside the git work tree")
    members = {*plan_paths, *commit.extra_paths, *(references_dir(item).rel for item in commit.items)}
    for member in members:
        relative = Path(member)
        if not member or member == "." or relative.is_absolute() or ".." in relative.parts:
            return CommitOutcome("failed", None, commit.subject, (), f"path is outside bundle: {member!r}")
    candidates = sorted({(bundle / member).relative_to(toplevel).as_posix() for member in members})
    pathspec, add_paths, discovery_error = _pathspec(toplevel, candidates) if candidates else ((), (), None)
    if discovery_error is not None:
        return CommitOutcome("failed", None, commit.subject, (), discovery_error)
    if not pathspec:
        return CommitOutcome("skipped", None, commit.subject, (), "no-changes")
    if add_paths:
        added = _git(toplevel, "add", "-A", "--", *add_paths)
        if added.returncode != 0:
            return CommitOutcome("failed", None, commit.subject, pathspec, _reason(added))
    staged = _git(toplevel, "diff", "--cached", "--quiet", "--", *pathspec)
    if staged.returncode == 0:
        return CommitOutcome("skipped", None, commit.subject, pathspec, "no-changes")
    if staged.returncode != 1:
        return CommitOutcome("failed", None, commit.subject, pathspec, _reason(staged))
    committed = _git(toplevel, "commit", "--only", "-m", commit.subject, "--", *pathspec)
    if committed.returncode != 0:
        return CommitOutcome("failed", None, commit.subject, pathspec, _reason(committed))
    head = probe_git(toplevel, "rev-parse", "HEAD")
    sha = head.stdout.strip() if head.returncode == 0 and head.stdout.strip() else None
    reason = None if sha is not None else f"{_reason(head)} reading HEAD"
    return CommitOutcome("committed", sha, commit.subject, pathspec, reason)


__all__ = [
    "COMMIT_FAILED_PREFIX",
    "NOTE_REASONS",
    "SUBJECT_PREFIX",
    "CommitMode",
    "CommitOutcome",
    "WorkspaceCommit",
    "commit_mode",
    "commit_target",
    "commit_workspace",
    "item_stem",
    "plan_paths",
]

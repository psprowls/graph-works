"""Git provenance: what a completed stage leaves behind.

The **only** module in this package that runs git. Every function is
best-effort and degrades to `None` (or a silent no-op): provenance capture is a
nice-to-have on the advance path, and an advance that failed because a `git`
subprocess timed out would be a worse outcome than one that stamped nothing.

The one exception is `write_active_work`'s `phase="done"` guard, which raises.
That is a caller bug, not a git failure: `done` is not an `ARTIFACT_PHASES`
member and `work_tracker_okf.paths.artifact_path` raises on it, so a `done`
pointer would crash the *next* transcript-capture hook rather than this one --
and a silent no-op there would hide the bug until it did.

No clock. `write_active_work` takes `updated=` as an already-formatted string,
matching `work-tracker-okf`'s `today=` convention: the caller that knows what
"now" means is the one that supplies it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from work_tracker_okf.results import ResultsFacts
from work_tracker_okf.vocabulary import ARTIFACT_PHASES

from graph_works_core.workspace.layout import WorkspaceLayout

#: The pointer a transcript-capture hook reads to attribute a session to an
#: item. It lands in `layout.cache_dir` -- gitignored machine state, which is
#: what the cache directory is for -- not in the committed config directory.
ACTIVE_WORK_FILENAME = "active-work.json"

#: Every git call is capped. A hung `git` on the advance path is the failure
#: this exists to make impossible.
_GIT_TIMEOUT_SECONDS = 5


def run_git(cwd: Path, *args: str) -> str | None:
    """Best-effort `git <args>` in *cwd*; stdout, or `None` on any failure.

    Public because `commands/orchestrate.py`'s `default_base` needs it: this is
    the only module in the package that runs git, and a second subprocess
    helper over there would be a second timeout policy and a second degrade
    contract to keep in step.
    """
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _absolute(raw: str, cwd: Path) -> Path | None:
    """A `git rev-parse --git-dir` answer, which may be relative to *cwd*."""
    if not raw.strip():
        return None
    candidate = Path(raw.strip())
    try:
        return (candidate if candidate.is_absolute() else cwd / candidate).resolve()
    except OSError:
        return None


def worktree_state(cwd: Path, repo: Path) -> tuple[str, str] | None:
    """`(toplevel, branch)` when *cwd* is a linked worktree of *repo*, else `None`.

    `--git-dir` differs from `--git-common-dir` in a linked worktree -- but also
    inside a submodule, so `--show-superproject-working-tree` rules that out.

    "Of *repo*" is decided by comparing *repo*'s own `--git-common-dir` with
    *cwd*'s: **two paths are the same repository exactly when their common dirs
    agree.** That is the property this docstring always claimed, stated
    directly instead of inferred from a directory layout. The previous check --
    the common dir's *parent* must be *repo* -- was only true when *repo* is
    the main checkout, so it answered `None` for a *repo* that is itself a
    linked worktree, and for either side using `--separate-git-dir`. A
    coordinator standing in a worktree is the ordinary case, not the exotic
    one.

    A detached HEAD returns `None`; there is no branch worth recording, and the
    finish stage creates one anyway.
    """
    git_dir_raw = run_git(cwd, "rev-parse", "--git-dir")
    common_raw = run_git(cwd, "rev-parse", "--git-common-dir")
    if git_dir_raw is None or common_raw is None:
        return None
    git_dir = _absolute(git_dir_raw, cwd)
    common = _absolute(common_raw, cwd)
    if git_dir is None or common is None or git_dir == common:
        return None
    if (run_git(cwd, "rev-parse", "--show-superproject-working-tree") or "").strip():
        return None
    repo_common_raw = run_git(Path(repo), "rev-parse", "--git-common-dir")
    if repo_common_raw is None:
        return None
    repo_common = _absolute(repo_common_raw, Path(repo))
    if repo_common is None or repo_common != common:
        return None
    top = run_git(cwd, "rev-parse", "--show-toplevel")
    branch = run_git(cwd, "branch", "--show-current")
    if top is None or branch is None or not branch.strip():
        return None
    return top.strip(), branch.strip()


def head_sha(repo: Path) -> str | None:
    """*repo*'s `HEAD`, or `None` when it is not a repo or has no commits."""
    out = run_git(repo, "rev-parse", "HEAD")
    sha = out.strip() if out is not None else ""
    return sha or None


def results_facts(repo: Path, *, phase: str, start_sha: str) -> ResultsFacts | None:
    """The facts a results stub renders from, gathered over `start_sha..HEAD`.

    Rendering and writing stay in `work_tracker_okf.results` -- that module
    declines git by name (`ResultsFacts` takes its facts, it never gathers
    them), and this is the gatherer it declined.
    """
    end_sha = head_sha(repo)
    if end_sha is None:
        return None
    diff = run_git(repo, "diff", "--name-status", f"{start_sha}..{end_sha}")
    log = run_git(repo, "log", "--oneline", f"{start_sha}..{end_sha}")
    if diff is None or log is None:
        return None
    files = tuple(line.split("\t")[-1] for line in diff.splitlines() if line.strip())
    commits = tuple(line for line in log.splitlines() if line.strip())
    return ResultsFacts(phase=phase, start_sha=start_sha, end_sha=end_sha, files=files, commits=commits)


def write_active_work(layout: WorkspaceLayout, slug: str, phase: str, *, updated: str) -> Path | None:
    """Stamp the active-work pointer; the path written, or `None` on `OSError`.

    Raises:
        ValueError: for a phase outside `ARTIFACT_PHASES` -- `done` most of all.
    """
    if phase not in ARTIFACT_PHASES:
        raise ValueError(
            f"phase {phase!r} produces no artifact, so it is not an active-work phase; "
            f"expected one of {list(ARTIFACT_PHASES)}"
        )
    pointer = {"slug": slug, "phase": phase, "updated": updated}
    target = layout.cache_dir / ACTIVE_WORK_FILENAME
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return None
    return target


def clear_active_work(layout: WorkspaceLayout, slugs: set[str]) -> bool:
    """Delete the pointer when it names one of *slugs*; whether it was deleted.

    Otherwise a later capture hook resurrects a working directory for an item
    that has moved to the archive. Fail-open on a missing, unreadable,
    malformed or non-mapping pointer -- the same degrade contract as the write.
    """
    target = layout.cache_dir / ACTIVE_WORK_FILENAME
    try:
        pointer = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(pointer, dict) or pointer.get("slug") not in slugs:
        return False
    try:
        target.unlink()
    except OSError:
        return False
    return True


__all__ = [
    "ACTIVE_WORK_FILENAME",
    "clear_active_work",
    "head_sha",
    "results_facts",
    "run_git",
    "worktree_state",
    "write_active_work",
]

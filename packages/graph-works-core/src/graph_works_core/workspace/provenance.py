"""Git provenance: what a completed stage leaves behind.

The **only** module in this package that runs git. Every function is
best-effort and degrades to `None` (or a silent no-op): provenance capture is a
nice-to-have on the advance path, and an advance that failed because a `git`
subprocess timed out would be a worse outcome than one that stamped nothing.

The exceptions are `write_active_work`'s canonical-path and dispatch-phase
guards, which raise. Those are caller bugs, not git failures: a coordination
pointer must identify exactly one path-native work item and an active phase.

No clock. `write_active_work` takes `updated=` as an already-formatted string,
matching `work-tracker-okf`'s `today=` convention: the caller that knows what
"now" means is the one that supplies it.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from work_tracker_okf.paths import parse_item_path
from work_tracker_okf.results import ResultsFacts
from work_tracker_okf.vocabulary import PHASES

from graph_works_core.workspace.layout import WorkspaceLayout

#: The pointer a transcript-capture hook reads to attribute a session to an
#: item. It lands in `layout.cache_dir` -- gitignored machine state, which is
#: what the cache directory is for -- not in the committed config directory.
ACTIVE_WORK_FILENAME = "active-work.json"
ACTIVE_WORK_PHASES = PHASES - {"done"}

#: Every git call is capped. A hung `git` on the advance path is the failure
#: this exists to make impossible.
_GIT_TIMEOUT_SECONDS = 5

#: git's own field separator for `--format`. Safe inside a subject line, where
#: a colon or a tab is not.
_UNIT_SEP = "\x1f"


def run_git(cwd: Path, *args: str) -> str | None:
    """Best-effort `git <args>` in *cwd*; stdout, or `None` on any failure.

    Public because callers outside this module run git through it: this is
    the only module in the package that does, and a second subprocess helper
    elsewhere would be a second timeout policy and a second degrade contract
    to keep in step.
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


#: What `default_base` answers when git cannot. The repo's own default branch
#: is the right answer and this is the fallback, not a preference.
FALLBACK_BASE = "main"


def default_base(repo: Path | None) -> str:
    """*repo*'s default branch, best-effort; `FALLBACK_BASE` on any failure.

    Lives here rather than in `orchestrate` because it runs git and this is the
    only module in the package that does — and because `workspace.anchor`,
    which sits below `orchestrate`, needs it to compute a merge base.
    """
    if repo is None:
        return FALLBACK_BASE
    out = run_git(repo, "symbolic-ref", "refs/remotes/origin/HEAD")
    if out is None or not out.strip():
        return FALLBACK_BASE
    return out.strip().rsplit("/", 1)[-1]


def merge_base(repo: Path, a: str, b: str) -> str | None:
    """The best common ancestor of *a* and *b* in *repo*, or `None`.

    Degrades like every other function here: an unknown ref, an unrelated
    history, or a directory that is not a repository all answer `None` rather
    than raising. `merge_base(default_base, HEAD)` on the base branch itself
    answers `HEAD` — an empty range, which `results.render()` already flags.
    """
    out = run_git(repo, "merge-base", a, b)
    sha = (out or "").strip()
    return sha or None


def spec_anchor_commit(repo: Path, spec_path: Path) -> str | None:
    """The most recent commit in *repo* touching *spec_path*.

    `None` when the file is untracked, new, or outside *repo*. Git's own
    history of the file IS the anchor when it has one, which is why no
    frontmatter field stamps it: it stays correct even if reconcile runs twice.
    """
    out = run_git(repo, "log", "-1", "--format=%H", "--", str(spec_path))
    sha = (out or "").strip()
    return sha or None


def commit_exists(repo: Path, sha: str) -> bool:
    """Whether *sha* names a commit reachable in *repo*. Never raises.

    The guard on a blank *sha* is not defensive noise: `cat-file -e ^{commit}`
    on an empty string is a git usage error, and this answers a question the
    caller asks about an unverified fallback.
    """
    if not sha:
        return False
    return run_git(repo, "cat-file", "-e", f"{sha}^{{commit}}") is not None


def commits_touching(repo: Path, commit_range: str, paths: Sequence[Path | str]) -> tuple[tuple[str, str], ...]:
    """`((sha, subject), ...)` for commits in *commit_range* touching *paths*,
    newest first.

    An empty *paths* returns `()` rather than the whole log — an unscoped range
    is never what the caller means, and silently widening it would flood the
    consumer with unrelated commits.
    """
    if not paths:
        return ()
    out = run_git(repo, "log", f"--format=%H{_UNIT_SEP}%s", commit_range, "--", *(str(path) for path in paths))
    rows: list[tuple[str, str]] = []
    for line in (out or "").splitlines():
        if _UNIT_SEP not in line:
            continue
        sha, subject = line.split(_UNIT_SEP, 1)
        rows.append((sha, subject))
    return tuple(rows)


def dirty_paths(repo: Path, paths: Sequence[Path | str]) -> tuple[str, ...] | None:
    """Paths under *paths* with uncommitted changes in *repo*, or `None`.

    `()` means "evaluated, and the tree is clean under this scope"; `None`
    means "could not be evaluated" -- an empty *paths*, a directory that is
    not a repository, or a `git status` that failed. The caller must
    distinguish them: the gate that reads this fails **open** on `None` and
    refuses on a non-empty tuple, so collapsing the two would either silence
    the gate or fire it on every non-repo workspace.

    Empty *paths* declines rather than widening to the whole tree, matching
    `commits_touching` and `results_facts` just above: an unscoped answer is
    never what the caller means, and here it would refuse an advance over dirt
    the item never claimed.

    `-z` rather than the default quoting: porcelain v1 escapes a path with a
    space or a non-ASCII byte into a C-quoted string, and a refusal that names
    `"spaced name.txt"` with the quotes attached is a path the reader cannot
    paste back into `git add`.
    """
    if not paths:
        return None
    out = run_git(repo, "status", "--porcelain", "-z", "--", *(str(path) for path in paths))
    if out is None:
        return None
    fields = out.split("\0")
    dirty: list[str] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        status, name = entry[:2], entry[3:]
        if status[0] in {"R", "C"}:
            # A rename or copy carries its source in the very next field.
            index += 1
        dirty.append(name)
    return tuple(dirty)


def results_facts(
    repo: Path,
    *,
    phase: str,
    start_sha: str,
    paths: Sequence[Path | str],
    opened: str,
) -> ResultsFacts | None:
    """The facts a results stub renders from, gathered over `start_sha..HEAD`,
    scoped to *paths*.

    An empty *paths* returns `None` -- no stub -- matching `commits_touching`'s
    own contract just above: we cannot attribute an item with no declared
    surface, and an unscoped page is worse than an absent one.

    Rendering and writing stay in `work_tracker_okf.results` -- that module
    declines git by name (`ResultsFacts` takes its facts, it never gathers
    them), and this is the gatherer it declined.
    """
    if not paths:
        return None
    end_sha = head_sha(repo)
    if end_sha is None:
        return None
    scope = tuple(str(path) for path in paths)
    diff = run_git(repo, "diff", "--name-status", f"{start_sha}..{end_sha}", "--", *scope)
    log = run_git(repo, "log", "--oneline", f"{start_sha}..{end_sha}", "--", *scope)
    if diff is None or log is None:
        return None
    files = tuple(line.split("\t")[-1] for line in diff.splitlines() if line.strip())
    commits = tuple(line for line in log.splitlines() if line.strip())
    return ResultsFacts(
        phase=phase,
        start_sha=start_sha,
        end_sha=end_sha,
        files=files,
        commits=commits,
        scope=scope,
        start_predates_item=_start_predates_opened(repo, start_sha, opened),
    )


def _start_predates_opened(repo: Path, start_sha: str, opened: str) -> bool:
    """Whether *start_sha*'s committer date is earlier than *opened*.

    Best-effort like every other call in this module: a failed `git show`
    degrades to `False` rather than making the range look wrong when we
    simply couldn't check. String comparison, not a datetime parse: both
    sides are ISO 8601 date-prefixed, which sorts correctly as text, and it
    keeps this a value comparison rather than a clock read.
    """
    out = run_git(repo, "show", "-s", "--format=%cI", start_sha)
    committer_date = (out or "").strip()
    if not committer_date:
        return False
    return committer_date[:10] < opened


def write_active_work(layout: WorkspaceLayout, path: str, phase: str, *, updated: str) -> Path | None:
    """Stamp the active-work pointer; the path written, or `None` on `OSError`.

    Raises:
        ValueError: for a noncanonical path or inactive phase -- `done` most of all.
    """
    if parse_item_path(path) is None:
        raise ValueError(f"expected a canonical work-item path, got {path!r}")
    if phase not in ACTIVE_WORK_PHASES:
        raise ValueError(
            f"phase {phase!r} produces no artifact, so it is not an active-work phase; "
            f"expected one of {sorted(ACTIVE_WORK_PHASES)}"
        )
    pointer = {"path": path, "phase": phase, "updated": updated}
    target = layout.cache_dir / ACTIVE_WORK_FILENAME
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return None
    return target


def clear_active_work(layout: WorkspaceLayout, paths: set[str]) -> bool:
    """Delete the pointer when it names one of *paths*; whether it was deleted.

    Otherwise a later capture hook resurrects a working directory for an item
    that has moved to the archive. Fail-open on a missing, unreadable,
    malformed or non-mapping pointer -- the same degrade contract as the write.
    """
    target = layout.cache_dir / ACTIVE_WORK_FILENAME
    try:
        pointer = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(pointer, dict):
        return False
    # Coordination cache is disposable. Pre-path-native records are invalid,
    # not aliases, and are removed instead of being interpreted as identity.
    path = pointer.get("path")
    if not isinstance(path, str) or parse_item_path(path) is None:
        try:
            target.unlink()
        except OSError:
            return False
        return True
    if path not in paths:
        return False
    try:
        target.unlink()
    except OSError:
        return False
    return True


__all__ = [
    "ACTIVE_WORK_FILENAME",
    "FALLBACK_BASE",
    "clear_active_work",
    "commit_exists",
    "commits_touching",
    "default_base",
    "dirty_paths",
    "head_sha",
    "merge_base",
    "results_facts",
    "run_git",
    "spec_anchor_commit",
    "worktree_state",
    "write_active_work",
]

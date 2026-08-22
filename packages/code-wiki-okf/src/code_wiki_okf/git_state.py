"""Minimal port of wiki-io's `git_state.py` — HEAD, cleanliness,
changed-files, and the tracked-file walk, plus the state gate.

Every function returns `None` on git unavailability rather than raising,
matching the precedent — a missing/broken git checkout degrades to
"unknown", never a crash.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


def _run(repo: Path, *args: str) -> tuple[int, str, str] | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return result.returncode, result.stdout, result.stderr


def head_commit(repo: Path) -> str | None:
    """Return the full HEAD SHA, or `None` if *repo* isn't a git checkout."""
    out = _run(repo, "rev-parse", "HEAD")
    if out is None or out[0] != 0:
        return None
    sha = out[1].strip()
    return sha or None


def is_clean_on_branches(repo: Path, branches: Sequence[str]) -> tuple[bool, str]:
    """`(True, "")` iff the working tree is clean AND HEAD is on a listed branch.

    Otherwise `(False, "<reason>")`. `branches` is the configured allow-list
    — `Config.state_gate.branches`.
    """
    branch_out = _run(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch_out is None or branch_out[0] != 0:
        return False, "not a git repo"
    branch = branch_out[1].strip()
    if branch not in branches:
        return False, f"branch is {branch!r}, not in {list(branches)}"
    status_out = _run(repo, "status", "--porcelain")
    if status_out is None or status_out[0] != 0:
        return False, "git status failed"
    if status_out[1].strip():
        return False, "working tree is dirty"
    return True, ""


def changed_files_since(repo: Path, since_sha: str, sub_paths: Sequence[str] = ()) -> list[str] | None:
    """Repo-relative paths under `sub_paths` changed between `since_sha` and HEAD.

    - `[]` when there are no changes.
    - `None` when git is unavailable, `since_sha` is unknown, or (when given)
      none of `sub_paths` are tracked.
    - `sub_paths` empty means no path filter — the whole repo.
    """
    if not since_sha:
        return None
    args = ["diff", "--name-only", f"{since_sha}..HEAD"]
    if sub_paths:
        args.append("--")
        args.extend(sub_paths)
    out = _run(repo, *args)
    if out is None or out[0] != 0:
        return None
    return [line.strip() for line in out[1].splitlines() if line.strip()]


def find_renames(repo: Path, base: str, head: str = "HEAD") -> list[tuple[str, str]] | None:
    """Renames git detects between `base` and `head`, as `(old_path, new_path)` pairs.

    `--find-renames` at git's own default similarity threshold — no override,
    matching `ls_files`'s stance that git owns the matching semantics, not
    this module. A file renamed and also edited is still reported: the
    threshold is about content similarity, not about the edit being zero.

    `None` when git is unavailable, `base` is not a known commit, or the
    command otherwise fails — the same contract `changed_files_since` uses
    for an unknown `since_sha`.
    """
    out = _run(repo, "diff", "--find-renames", "--name-status", "--diff-filter=R", f"{base}..{head}")
    if out is None or out[0] != 0:
        return None
    renames: list[tuple[str, str]] = []
    for line in out[1].splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            continue
        _status, old_path, new_path = fields
        renames.append((old_path, new_path))
    return renames


def earliest_common_ancestor(repo: Path, shas: Sequence[str]) -> str | None:
    """The one commit in *shas* that predates the others -- git's own answer,
    never a string comparison.

    SHA-1 hex digests carry no relation to commit order, so picking the
    lexicographically (equivalently, numerically) smallest of a commit set is
    not "the oldest" -- it is an arbitrary pick with no correlation to git
    history at all. `git merge-base --octopus` is git's own primitive for
    "which commit is an ancestor of every one of these", immune to both SHA
    ordering and committer-clock skew. A single sha is passed twice rather
    than routed around the command -- `merge-base --octopus <sha> <sha>` both
    answers "itself" for a real commit and still fails for an unknown one,
    where `rev-parse --verify <sha>` alone would not: it accepts any
    syntactically well-formed 40-hex string without checking the object
    actually exists.

    `None` when git is unavailable, any sha is unknown, or the set shares no
    common ancestor -- matching every other `git_state` function's contract
    for "can't answer this".
    """
    if not shas:
        return None
    unique = sorted(set(shas))
    args = unique if len(unique) > 1 else [unique[0], unique[0]]
    out = _run(repo, "merge-base", "--octopus", *args)
    if out is None or out[0] != 0:
        return None
    result = out[1].strip()
    return result or None


def ls_files(repo: Path, exclude: Sequence[str] = ()) -> list[str] | None:
    """Tracked files, minus `exclude` git glob pathspecs.

    Each `exclude` entry becomes a `:(exclude,glob)<pattern>` pathspec — git
    owns the matching semantics, no matcher code lives here. `None` when git
    is unavailable or the command fails.
    """
    args = ["ls-files"]
    if exclude:
        args.append("--")
        args.extend(f":(exclude,glob){pattern}" for pattern in exclude)
    out = _run(repo, *args)
    if out is None or out[0] != 0:
        return None
    return sorted(line.strip() for line in out[1].splitlines() if line.strip())


@dataclass(frozen=True, slots=True)
class StateGate:
    """May this run stamp state?"""

    allowed: bool
    reason: str
    head_commit: str | None


def compute_state_gate(repo: Path, *, enabled: bool, branches: Sequence[str]) -> StateGate:
    """Answer "may this run stamp state?" from `Config.state_gate`.

    A disabled gate is always allowed; the reason says so. An enabled gate
    defers to `is_clean_on_branches()`.
    """
    if not enabled:
        return StateGate(allowed=True, reason="state gate disabled in workspace.yaml", head_commit=head_commit(repo))
    ok, reason = is_clean_on_branches(repo, branches)
    return StateGate(allowed=ok, reason=reason or "clean and on an allowed branch", head_commit=head_commit(repo))

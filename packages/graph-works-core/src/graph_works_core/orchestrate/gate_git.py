"""The gate's git reads. Strict: every failure is `GitUnavailable`, never a pass."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from graph_works_core.workspace import provenance


class GitUnavailable(Exception):
    """git could not answer; the message carries the cause."""


@dataclass(frozen=True, slots=True)
class TreeSnapshot:
    head: str
    tree: str
    dirty: tuple[str, ...]


def _resolve(git: provenance.GitExecutable | None) -> provenance.GitExecutable:
    if git is not None:
        return git
    resolved = provenance.resolve_git(None, environ=os.environ)
    if isinstance(resolved, provenance.GitFailure):
        raise GitUnavailable(f"git unavailable ({resolved.cause}): {resolved.detail}")
    return resolved


def _git(cwd: Path, *args: str, git: provenance.GitExecutable | None) -> str:
    executable = _resolve(git)
    out = provenance.strict_git(cwd, *args, git=executable)
    if isinstance(out, provenance.GitFailure):
        raise GitUnavailable(f"git unavailable ({out.cause}): {out.detail}")
    return out


def snapshot(worktree: Path, *, git: provenance.GitExecutable | None = None) -> TreeSnapshot:
    """HEAD, its tree, and every dirty or untracked path (`()` means clean)."""
    head = _git(worktree, "rev-parse", "--verify", "HEAD^{commit}", git=git).strip()
    tree = _git(worktree, "rev-parse", "--verify", "HEAD^{tree}", git=git).strip()
    fields = _git(worktree, "status", "--porcelain", "-z", "--untracked-files=all", git=git).split("\0")
    dirty: list[str] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        if entry[0] in {"R", "C"}:
            index += 1  # a rename or copy carries its source in the next field
        dirty.append(entry[3:])
    return TreeSnapshot(head, tree, tuple(dirty))


def _common_dir(path: Path, git: provenance.GitExecutable | None) -> Path:
    raw = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir", git=git).strip()
    return Path(raw).resolve()


def same_repository(worktree: Path, repo: Path, *, git: provenance.GitExecutable | None = None) -> bool:
    return _common_dir(worktree, git) == _common_dir(repo, git)


def gate_log_dir(worktree: Path, item_slug: str, *, git: provenance.GitExecutable | None = None) -> Path:
    """`<git-dir>/gw-gate/<item_slug>`: inside the git directory, so never dirty."""
    raw = _git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "gw-gate", git=git).strip()
    return Path(raw) / item_slug


__all__ = ["GitUnavailable", "TreeSnapshot", "gate_log_dir", "same_repository", "snapshot"]

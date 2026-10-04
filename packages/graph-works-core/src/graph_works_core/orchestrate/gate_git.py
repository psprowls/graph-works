"""The gate's git reads. Strict: every failure is `GitUnavailable`, never a pass."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

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


def _git(cwd: Path, *args: str, git: provenance.GitExecutable | None, preserve_output: bool = False) -> str:
    executable = _resolve(git)
    out = (
        provenance.strict_git(cwd, *args, git=executable, preserve_output=True)
        if preserve_output
        else provenance.strict_git(cwd, *args, git=executable)
    )
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


@dataclass(frozen=True, slots=True)
class GitLeaf:
    """A Git leaf identity: the mode encodes file kind and executable permission."""

    mode: str
    object_sha: str


def ls_tree(worktree: Path, tree: str, *, git: provenance.GitExecutable | None = None) -> dict[str, GitLeaf]:
    """Every leaf of *tree*: POSIX path -> mode and object SHA, including symlinks and gitlinks.

    Gitlinks contribute their pinned commit IDs; omitting them would hide changed
    submodule inputs. NUL framing preserves whitespace and newlines in filenames.
    """
    raw = _git(worktree, "ls-tree", "-r", "-z", "--full-tree", tree, git=git, preserve_output=True)
    if not raw:
        return {}
    if not raw.endswith("\0"):
        raise GitUnavailable("git ls-tree: unterminated record")
    listing: dict[str, GitLeaf] = {}
    modes = {"100644": "blob", "100755": "blob", "120000": "blob", "160000": "commit"}
    for record in raw[:-1].split("\0"):
        meta, sep, path = record.partition("\t")
        fields = meta.split(" ")
        pure = PurePosixPath(path)
        if (
            not sep
            or not path
            or len(fields) != 3
            or modes.get(fields[0]) != fields[1]
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", fields[2]) is None
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or path in listing
        ):
            raise GitUnavailable(f"git ls-tree: malformed record {record!r}")
        listing[path] = GitLeaf(fields[0], fields[2])
    return listing


def _common_dir(path: Path, git: provenance.GitExecutable | None) -> Path:
    raw = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir", git=git).strip()
    return Path(raw).resolve()


def same_repository(worktree: Path, repo: Path, *, git: provenance.GitExecutable | None = None) -> bool:
    return _common_dir(worktree, git) == _common_dir(repo, git)


def gate_log_dir(worktree: Path, item_slug: str, *, git: provenance.GitExecutable | None = None) -> Path:
    """`<git-dir>/gw-gate/<item_slug>`: inside the git directory, so never dirty."""
    raw = _git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "gw-gate", git=git).strip()
    return Path(raw) / item_slug


__all__ = ["GitLeaf", "GitUnavailable", "TreeSnapshot", "gate_log_dir", "ls_tree", "same_repository", "snapshot"]

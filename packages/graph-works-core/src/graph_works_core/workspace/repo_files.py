"""What files a declared code repository holds, and whether a path stays inside one.

Layer 0 because two verticals ask it: `wiki_page` resolves citations against
every repository's file set, and `code_read` serves an excerpt from one. The
verticals-never-import-each-other contract forbids either owning it.

A repository's file set is its **tracked** files (`git ls-files`) minus its
merged `ignore:` globs, matched by `code_graph_io`'s compiler so a pattern
means here what it means to the scanner. An untracked or ignored file (a
`.env`, build output) is therefore never resolvable and never served.

Nothing here raises for a repository's state: a missing directory, a plain
directory, or a `git` that fails or is absent yields an empty set. Git runs
through `provenance.run_git`, the package's one git helper.

Display reads can reuse an inventory from the disposable display cache. Its
stat-only key covers the resolved root, ignore globs, `.git` directory or file
target, and index size, nanosecond mtime and inode. HEAD is absent because
`git ls-files` lists index entries (D-002). An index less than two seconds old
(including a future mtime) is racy: its inventory is always re-listed and never
memoized. The bounded process memo still recomputes the key on every call.
No cache handle, disabled caching, or a Git environment override computes fresh.
Key IO errors and failed Git probes never store an inventory.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from code_graph_io import compile_ignore
from code_wiki_okf.config import RepoConfig

from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.display_cache import DisplayCache, open_display_cache
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.provenance import run_git

Confinement = Literal["outside-repository", "unknown-file"]
RACY_NS = 2_000_000_000
GIT_ENV_OVERRIDES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
_MEMO: dict[tuple[Path, str], frozenset[str]] = {}


@dataclass(frozen=True, slots=True)
class RepoFiles:
    """One declared repository: its name, real root, and repo-relative POSIX file set."""

    name: str
    root: Path
    files: frozenset[str]


def declared_repos(layout: WorkspaceLayout) -> tuple[RepoConfig, ...]:
    """Every declared repository in manifest order; `()` when the manifest is absent.

    A malformed manifest raises `WorkspaceError`, as `repos.resolve_repos` does.
    """
    try:
        return load_workspace_config(layout).repos
    except OSError:
        return ()


def _is_work_tree_root(root: Path) -> bool:
    top = run_git(root, "rev-parse", "--show-toplevel")
    return top is not None and Path(top.strip()).resolve() == root


def _list(root: Path, ignore: Sequence[str]) -> frozenset[str] | None:
    """List fresh; distinguish failed probes from a successful empty inventory."""
    if not _is_work_tree_root(root):
        return None
    listing = run_git(root, "ls-files", "-z")
    if listing is None:
        return None
    compiled = compile_ignore(ignore)
    return frozenset(p for p in listing.split("\0") if p and not compiled.matches(p))


def inventory_key(root: Path, ignore: Sequence[str]) -> str | None:
    """Canonical stat key, or `None` without a `.git` entry; other IO errors propagate."""
    dot_git = root / ".git"
    try:
        st = dot_git.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISDIR(st.st_mode):
        git = ["dir", dot_git.as_posix()]
        gitdir = dot_git
    else:
        text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        target = text.removeprefix("gitdir:").strip() if text.startswith("gitdir:") else ""
        gitdir = (root / target).resolve() if target else dot_git
        git = ["file", gitdir.as_posix()]
    try:
        ist = (gitdir / "index").stat()
        index: list[int] | None = [ist.st_size, ist.st_mtime_ns, ist.st_ino]
    except FileNotFoundError:
        index = None
    ignore_fp = hashlib.sha256(
        json.dumps(list(ignore), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    payload = {"root": root.as_posix(), "ignore": ignore_fp, "git": git, "index": index}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _is_racy(key: str) -> bool:
    index = json.loads(key)["index"]
    return index is not None and time.time_ns() - index[1] < RACY_NS


def _remember(root: Path, key: str, files: frozenset[str]) -> None:
    if len(_MEMO) >= 64:
        _MEMO.clear()
    _MEMO[root, key] = files


def repo_file_set(repo: RepoConfig, *, cache: DisplayCache | None = None) -> RepoFiles:
    """*repo*'s tracked files minus its ignore globs; empty when it is not a work-tree root."""
    root = repo.path.resolve()
    if not root.is_dir():
        return RepoFiles(repo.name, root, frozenset())
    if cache is None or any(name in os.environ for name in GIT_ENV_OVERRIDES):
        return RepoFiles(repo.name, root, _list(root, repo.ignore) or frozenset())
    try:
        key = inventory_key(root, repo.ignore)
    except OSError:
        return RepoFiles(repo.name, root, _list(root, repo.ignore) or frozenset())
    if key is None:
        return RepoFiles(repo.name, root, frozenset())
    memo = _MEMO.get((root, key))
    if memo is not None:
        return RepoFiles(repo.name, root, memo)
    stored = cache.inventory(root, key)
    if stored is not None and not stored[1]:
        _remember(root, key, stored[0])
        return RepoFiles(repo.name, root, stored[0])
    files = _list(root, repo.ignore)
    if files is None:
        return RepoFiles(repo.name, root, frozenset())
    try:
        again = inventory_key(root, repo.ignore)
    except OSError:
        again = None
    if again != key:
        return RepoFiles(repo.name, root, files)
    racy = _is_racy(key)
    cache.store_inventory(root, key, files, racy=racy)
    if not racy:
        _remember(root, key, files)
    return RepoFiles(repo.name, root, files)


def repo_file_sets_in(layout: WorkspaceLayout, cache: DisplayCache | None) -> tuple[RepoFiles, ...]:
    """List every declared repository using an already-open display cache."""
    return tuple(repo_file_set(repo, cache=cache) for repo in declared_repos(layout))


def repo_file_sets(layout: WorkspaceLayout) -> tuple[RepoFiles, ...]:
    """`repo_file_set` for every declared repository, in manifest order."""
    repos = declared_repos(layout)
    if not repos:
        return ()
    with open_display_cache(layout) as cache:
        return tuple(repo_file_set(repo, cache=cache) for repo in repos)


def confine(repo: RepoFiles, path: str) -> Path | Confinement:
    """The resolved file *path* names inside *repo*, or why it may not be read.

    Order matters: an escape is `outside-repository` even when the target
    exists, so a caller can never probe outside the root by status. Both the
    requested spelling and resolved target must be tracked and not ignored.
    """
    posix = PurePosixPath(path)
    if "\\" in path or posix.is_absolute() or PureWindowsPath(path).drive or ".." in posix.parts:
        return "outside-repository"
    try:
        resolved = (repo.root / path).resolve()
    except OSError:
        return "unknown-file"
    if not resolved.is_relative_to(repo.root):
        return "outside-repository"
    if path not in repo.files or resolved.relative_to(repo.root).as_posix() not in repo.files or not resolved.is_file():
        return "unknown-file"
    return resolved


__all__ = [
    "Confinement",
    "RepoFiles",
    "confine",
    "declared_repos",
    "inventory_key",
    "repo_file_set",
    "repo_file_sets",
    "repo_file_sets_in",
]

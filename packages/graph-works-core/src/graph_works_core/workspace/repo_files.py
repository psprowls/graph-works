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
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from code_graph_io import compile_ignore
from code_wiki_okf.config import RepoConfig

from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.provenance import run_git

Confinement = Literal["outside-repository", "unknown-file"]


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


def repo_file_set(repo: RepoConfig) -> RepoFiles:
    """*repo*'s tracked files minus its ignore globs; empty when it is not a work-tree root."""
    root = repo.path.resolve()
    if not root.is_dir() or not _is_work_tree_root(root):
        return RepoFiles(repo.name, root, frozenset())
    listing = run_git(root, "ls-files", "-z")
    if listing is None:
        return RepoFiles(repo.name, root, frozenset())
    ignore = compile_ignore(repo.ignore)
    return RepoFiles(repo.name, root, frozenset(p for p in listing.split("\0") if p and not ignore.matches(p)))


def repo_file_sets(layout: WorkspaceLayout) -> tuple[RepoFiles, ...]:
    """`repo_file_set` for every declared repository, in manifest order."""
    return tuple(repo_file_set(repo) for repo in declared_repos(layout))


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


__all__ = ["Confinement", "RepoFiles", "confine", "declared_repos", "repo_file_set", "repo_file_sets"]

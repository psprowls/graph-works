"""The layout object — the one thing in the rebuild that knows where a
workspace keeps its parts.

There are deliberately **no module-level path functions** here: no
`graph_dir(workspace)`, no `bundle_dir(workspace)`. A consumer receives a
`WorkspaceLayout`, or one member of it, as an argument. `layout_for` is the
single constructor, not a lookup — `discovery.resolve` and `init.plan_init`
call it, and nothing below band 3 does.

`repo_root` is **the repo the workspace lives in** — used for gitignore
placement and worktree roots, and `None` when the workspace sits outside any
repository. It is *not* the scan target: scan targets are declared, with
paths, in `_repositories.yaml`. Naming the distinction in the type is what
stops a caller with one `Path` to hand from conflating them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: The workspace marker. A fixed name, because a file cannot be relocated by a
#: key it contains.
MANIFEST_FILENAME = "workspace.yaml"

#: What a `.git` walk-up defaults to: `<repo>/.works`.
DEFAULT_WORKSPACE_NAME = ".works"

#: The one directory every graph-works-owned member nests under, except
#: `workspace.yaml` itself (the discovery anchor, D1) and `bundle_dir` (the
#: human's content, D5). Not an override key -- there is no `WorkspaceLayout`
#: field for it, only this shared literal prefix and the fixed location
#: `.gitignore` nests under.
GW_DIRNAME = "_gw"

DEFAULT_BUNDLE_DIR = "okf"
DEFAULT_CACHE_DIR = f"{GW_DIRNAME}/_cache"
DEFAULT_CONFIG_DIR = f"{GW_DIRNAME}/_config"
DEFAULT_WORKTREES_DIR = f"{GW_DIRNAME}/worktrees"
DEFAULT_REPOSITORIES_PATH = f"{GW_DIRNAME}/_repositories.yaml"


def _member(root: Path, raw: str) -> Path:
    """One override resolved against *root*. An absolute (or `~`) override is
    honored as given, which is what lets a cache live on another volume."""
    expanded = Path(raw).expanduser()
    return expanded if expanded.is_absolute() else root / expanded


def _relative(path: Path, base: Path) -> str | None:
    """*path* under *base* as a posix string, or None when it is not under it."""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class WorkspaceLayout:
    """Where one workspace keeps its parts, fully resolved."""

    root: Path
    config_dir: Path
    cache_dir: Path
    bundle_dir: Path
    worktrees_dir: Path
    repositories_path: Path
    repo_root: Path | None = None

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILENAME

    @property
    def directories(self) -> tuple[Path, ...]:
        """Every directory an init creates, in creation order."""
        return (self.root, self.config_dir, self.cache_dir, self.bundle_dir, self.worktrees_dir)

    @property
    def gitignore_entries(self) -> tuple[str, ...]:
        """Lines for `<root>/_gw/.gitignore` — the gitignored members, relative
        to `_gw/` rather than the workspace root.

        Derived from the resolved members rather than hard-coded, so an
        override moves the entry with the directory. A member relocated
        outside `_gw/` contributes nothing: `<root>/_gw/.gitignore` cannot
        ignore what is not under it -- the same "outside the anchor, not our
        problem" behavior this had relative to `root` before, now scoped one
        level deeper.
        """
        gw_root = self.root / GW_DIRNAME
        relatives = (_relative(self.cache_dir, gw_root), _relative(self.worktrees_dir, gw_root))
        return tuple(f"/{relative}/" for relative in relatives if relative is not None)

    @property
    def scanner_excludes(self) -> tuple[str, ...]:
        """Repo-relative git pathspecs for `_repositories.yaml`'s `ignore:`.

        In the default layout the workspace sits inside the repo it describes,
        and a scan that walks its own output grows every run. Empty when the
        workspace is outside its repo, or outside any repo.
        """
        if self.repo_root is None:
            return ()
        relative = _relative(self.root, self.repo_root)
        return () if relative is None else (f"{relative}/**",)


def layout_for(
    root: str | Path,
    *,
    bundle_dir: str = DEFAULT_BUNDLE_DIR,
    config_dir: str = DEFAULT_CONFIG_DIR,
    cache_dir: str = DEFAULT_CACHE_DIR,
    worktrees_dir: str = DEFAULT_WORKTREES_DIR,
    repositories_path: str = DEFAULT_REPOSITORIES_PATH,
    repo_root: str | Path | None = None,
) -> WorkspaceLayout:
    """Build the layout for *root*, applying the manifest's five overrides.

    The single constructor, called by `discovery.resolve` and `init.plan_init`.
    It is not a path lookup: it takes every override at once and returns the
    whole object, so there is never a call site asking this module for one
    directory.
    """
    resolved = Path(root).expanduser().resolve()
    return WorkspaceLayout(
        root=resolved,
        config_dir=_member(resolved, config_dir),
        cache_dir=_member(resolved, cache_dir),
        bundle_dir=_member(resolved, bundle_dir),
        worktrees_dir=_member(resolved, worktrees_dir),
        repositories_path=_member(resolved, repositories_path),
        repo_root=None if repo_root is None else Path(repo_root).expanduser().resolve(),
    )


__all__ = [
    "DEFAULT_BUNDLE_DIR",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_REPOSITORIES_PATH",
    "DEFAULT_WORKSPACE_NAME",
    "DEFAULT_WORKTREES_DIR",
    "GW_DIRNAME",
    "MANIFEST_FILENAME",
    "WorkspaceLayout",
    "layout_for",
]

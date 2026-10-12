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
paths, in the workspace manifest's `repositories:` block. Naming the
distinction in the type is what stops a caller with one `Path` to hand from
conflating them.

`local_manifest_path` is its gitignored, per-machine sibling — same directory,
different lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: The workspace marker. A fixed name, because a file cannot be relocated by a
#: key it contains.
MANIFEST_FILENAME = "workspace.yaml"

#: The per-machine overlay, gitignored and never committed. A sibling of the
#: manifest rather than a member of `config_dir`, so both files are read from
#: the same directory and a relative path in either anchors identically
#: (ADR 2026-08-26-a-relative-path).
LOCAL_MANIFEST_FILENAME = "workspace.local.yaml"

#: What a `.git` walk-up defaults to: `<repo>/.works`.
DEFAULT_WORKSPACE_NAME = ".works"

#: The control-plane directory. Also `config_dir`'s default value — not an
#: independent anchor, `config_dir` is: `WorkspaceLayout.gitignore_entries`
#: anchors at `self.config_dir`, whatever that resolves to, and `.gw` is only
#: what it resolves to when nobody overrides it.
GW_DIRNAME = ".gw"

#: Relative names, not paths — resolved against the *resolved* `config_dir`,
#: not the workspace root, so an override of `config_dir` carries its
#: derived members with it.
CACHE_DIRNAME = "cache"
WORKTREES_DIRNAME = "worktrees"

DEFAULT_BUNDLE_DIR = "okf"
DEFAULT_CONFIG_DIR = GW_DIRNAME


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
    repo_root: Path | None = None

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILENAME

    @property
    def local_manifest_path(self) -> Path:
        return self.root / LOCAL_MANIFEST_FILENAME

    @property
    def directories(self) -> tuple[Path, ...]:
        """Every directory an init creates, in creation order."""
        return (self.root, self.config_dir, self.cache_dir, self.bundle_dir, self.worktrees_dir)

    @property
    def gitignore_entries(self) -> tuple[str, ...]:
        """Lines for `<config_dir>/.gitignore` — the gitignored members,
        relative to `config_dir` rather than the workspace root.

        Derived from the resolved members rather than hard-coded, so an
        override moves the entry with the directory. A member relocated
        outside `config_dir` contributes nothing: `<config_dir>/.gitignore`
        cannot ignore what is not under it -- the same "outside the anchor,
        not our problem" behavior this had relative to `root` before, now
        anchored at `config_dir` instead of a fixed `GW_DIRNAME` literal.
        """
        relatives = (_relative(self.cache_dir, self.config_dir), _relative(self.worktrees_dir, self.config_dir))
        return tuple(f"/{relative}/" for relative in relatives if relative is not None)

    @property
    def scanner_excludes(self) -> tuple[str, ...]:
        """Repo-relative git pathspecs for the workspace manifest's `ignore:`.

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
    cache_dir: str | None = None,
    worktrees_dir: str | None = None,
    repo_root: str | Path | None = None,
) -> WorkspaceLayout:
    """Build the layout for *root*, applying the manifest's four overrides.

    The single constructor, called by `discovery.resolve` and `init.plan_init`.
    It is not a path lookup: it takes every override at once and returns the
    whole object, so there is never a call site asking this module for one
    directory.

    `cache_dir`/`worktrees_dir` default to `None`, meaning "derive from the
    resolved `config_dir`" — `config_dir / CACHE_DIRNAME` /
    `config_dir / WORKTREES_DIRNAME`. Resolving them against `config_dir`
    (already resolved from *its own* override) rather than against a literal
    is what makes relocating `config_dir` move the whole control plane as a
    unit, and what makes `gitignore_entries`' anchor always cover its own
    derived members.
    """
    resolved = Path(root).expanduser().resolve()
    resolved_config_dir = _member(resolved, config_dir)
    return WorkspaceLayout(
        root=resolved,
        config_dir=resolved_config_dir,
        cache_dir=(resolved_config_dir / CACHE_DIRNAME if cache_dir is None else _member(resolved, cache_dir)),
        bundle_dir=_member(resolved, bundle_dir),
        worktrees_dir=(
            resolved_config_dir / WORKTREES_DIRNAME if worktrees_dir is None else _member(resolved, worktrees_dir)
        ),
        repo_root=None if repo_root is None else Path(repo_root).expanduser().resolve(),
    )


__all__ = [
    "CACHE_DIRNAME",
    "DEFAULT_BUNDLE_DIR",
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_WORKSPACE_NAME",
    "GW_DIRNAME",
    "LOCAL_MANIFEST_FILENAME",
    "MANIFEST_FILENAME",
    "WORKTREES_DIRNAME",
    "WorkspaceLayout",
    "layout_for",
]

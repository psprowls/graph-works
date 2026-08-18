"""Workspace resolution: explicit argument -> `GRAPH_WORKS_DIR` -> `.git` walk-up.

Discovery resolves **the workspace**, and nothing else. It does not discover
repositories: those are declared, with paths, in `_repositories.yaml`.

Three more things are dropped deliberately rather than overlooked:

- **The old workspace pointer** — honoring the legacy environment variable
  would resolve a path and then fail one call later when the layout it names
  is incompatible with this package's expectations.
- **The legacy `.graph-wiki.local.yaml` warning** — it warns about a pointer
  file that only exists in the old repository, which is read-only and
  untouched.
- **The `repo-directory:` pin** — repos are declared, with paths, in
  `_repositories.yaml`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from graph_works_core.workspace.layout import DEFAULT_WORKSPACE_NAME, MANIFEST_FILENAME, WorkspaceLayout, layout_for
from graph_works_core.workspace.manifest import WORKSPACE_DIR_ENV, read


def find_repo_root(start: str | Path) -> Path | None:
    """The nearest ancestor of *start* (inclusive) holding a `.git`, or None.

    This answers "which repo does this path live in", which is what gitignore
    placement and worktree roots need. It is not a scan target.
    """
    resolved = Path(start).expanduser().resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _root(workspace: str | Path | None, cwd: str | Path | None, environ: Mapping[str, str]) -> Path:
    if workspace is not None:
        return Path(workspace).expanduser().resolve()
    pinned = environ.get(WORKSPACE_DIR_ENV, "").strip()
    if pinned:
        return Path(pinned).expanduser().resolve()
    start = Path.cwd() if cwd is None else Path(cwd).expanduser().resolve()
    repo_root = find_repo_root(start)
    return (repo_root if repo_root is not None else start) / DEFAULT_WORKSPACE_NAME


def resolve(
    *,
    workspace: str | Path | None = None,
    cwd: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    repo_root: str | Path | None = None,
) -> WorkspaceLayout:
    """Resolve the workspace for this invocation, as a layout.

    Precedence: *workspace* -> `GRAPH_WORKS_DIR` -> a `.git` walk-up from
    *cwd*, defaulting to `<repo>/.works`. The marker is
    `<root>/workspace.yaml`; when it is absent, `WorkspaceNotFound` names the
    bootstrap call. The four layout members come from the manifest's
    overrides, defaulted.

    *environ* defaults to the process environment and is a parameter for the
    same reason `config_io.resolve_key` requires one: a test that pins a
    workspace should not have to mutate the process to do it.

    *repo_root* mirrors `init.plan_init`'s parameter of the same name and
    exists for the same reason: a workspace outside the repo it catalogs is a
    walk-up `find_repo_root` cannot find. Nothing persists a `repo_root` given
    at init, so without this parameter every later `resolve()` silently
    re-derives one, and for that workspace shape re-deriving means `None` —
    not the repo `_repositories.yaml` actually names. A caller that already
    knows the intended repo (because it read it from `_repositories.yaml`, or
    because it is the same caller that pinned it at init) passes it here
    instead of losing it to the walk-up again.
    """
    resolved_environ = os.environ if environ is None else environ
    root = _root(workspace, cwd, resolved_environ)
    manifest = read(root / MANIFEST_FILENAME, environ=resolved_environ)
    return layout_for(
        root,
        bundle_dir=manifest.bundle_dir,
        config_dir=manifest.config_dir,
        cache_dir=manifest.cache_dir,
        worktrees_dir=manifest.worktrees_dir,
        repo_root=Path(repo_root).expanduser().resolve() if repo_root is not None else find_repo_root(root),
    )


__all__ = ["find_repo_root", "resolve"]

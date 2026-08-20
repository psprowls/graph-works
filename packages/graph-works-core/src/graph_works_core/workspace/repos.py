"""Which code repository this workspace describes.

One question, one function. It sits at layer 0 rather than inside
`orchestrate/` because two verticals now ask it — `orchestrate` for dispatch,
`work` for reconciliation evidence — and the verticals-never-import-each-other
contract in the root `pyproject.toml` forbids the second reaching across for
the first's copy.

It is not folded into `discovery.py`, which answers "where is the workspace".
This answers "what code does that workspace catalog": a different question,
with a different failure mode (refusal on ambiguity, not a walk-up).
"""

from __future__ import annotations

from pathlib import Path

from code_wiki_okf.config import CONFIG_FILENAME, ConfigError, load_config

from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout


def resolve_repo(layout: WorkspaceLayout, *, repo_name: str | None = None) -> tuple[Path | None, str | None]:
    """The code repo this workspace describes, and why there is none.

    `_repositories.yaml` is authoritative **always**, not only when the
    workspace and the code repo are separate repositories. The layout's
    `repo_root` is a `.git` walk-up from the workspace root: in the split
    topology it resolves to the *vault*, and every git call made against it
    then degrades to `None` without a word. The declarations file is the
    surface that actually names the code.

    Six outcomes, closed:

    - Exactly one declared, no *repo_name* -> that repo.
    - *repo_name* names a declared repo -> that repo.
    - *repo_name* names nothing declared -> `WorkspaceError`, naming the set.
    - Several declared, no *repo_name* -> `WorkspaceError`. Ambiguity refuses
      rather than guessing; a wrong repo is worse than a refusal.
    - Zero declared, or the file missing -> `(None, note)`. Not an error: a
      workspace that catalogs no code is a shape.
    - Malformed -> `WorkspaceError`. Config raises and content never does, the
      line `code_wiki_okf.config` draws for itself and `_routing_rules` draws
      for a hand-edited manifest.
    """
    path = layout.bundle_dir / CONFIG_FILENAME
    try:
        config = load_config(layout.bundle_dir)
    except OSError:
        return None, f"{path}: absent, so this workspace declares no code repository"
    except ConfigError as exc:
        raise WorkspaceError(str(exc)) from exc

    declared = sorted(entry.name for entry in config.repos)
    if repo_name is not None:
        match = next((entry for entry in config.repos if entry.name == repo_name), None)
        if match is None:
            raise WorkspaceError(f"{path}: repo_name {repo_name!r} names no declared repository; declared: {declared}")
        return match.path, None
    if not config.repos:
        return None, f"{path}: declares no repositories, so no code repo was resolved"
    if len(config.repos) > 1:
        raise WorkspaceError(
            f"{path}: {len(config.repos)} repositories declared ({declared}); pass repo_name= to choose one"
        )
    return config.repos[0].path, None


__all__ = ["resolve_repo"]

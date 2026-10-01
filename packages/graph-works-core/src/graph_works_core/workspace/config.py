"""Load the declarations/configuration owned by a workspace layout.

Interface consumers depend on this adapter rather than reaching through core
to ``code_wiki_okf.config``. Schema/configuration failures are translated to
the workspace error taxonomy; filesystem failures remain ``OSError``.

This is where the workspace's one reader and its one validator meet: the
bytes come from ``config-io``'s store (so nothing in the workspace parses
``workspace.yaml`` twice, with two dialects), and the three declaration
blocks are validated by ``code_wiki_okf``. It is the only band-3 caller of
``config_from_mapping``.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Mapping
from pathlib import Path

from code_wiki_okf.config import Config, ConfigError, config_from_mapping
from config_io import StoreValidationError

from graph_works_core.workspace.errors import WorkspaceConfigError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import workspace_store

WorkspaceConfig = Config


def load_workspace_config(layout: WorkspaceLayout) -> WorkspaceConfig:
    """Load the workspace's declarations with layout-derived paths.

    Raises `FileNotFoundError` when the manifest is absent,
    `WorkspaceConfigError` for anything the store or the validator refuses.
    """
    path = layout.manifest_path
    if not path.exists():
        # Load-bearing, not defensive. `code_wiki_okf.load_config` propagated
        # `OSError` for a missing file; `PlainYamlStore._load` returns `{}`.
        # `workspace/repos.py` distinguishes the two outcomes and
        # `test_repos.py` pins it, so the absence has to raise here. Going
        # through `errno.ENOENT` reproduces `read_bytes()`'s message exactly,
        # so nothing downstream sees a new sentence.
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(path))
    try:
        content = workspace_store(layout).read_explicit()
    except StoreValidationError as exc:
        # The parse moved into the store, so "not valid YAML" and "not a
        # mapping at the top level" now arrive from here rather than from
        # `load_config`.
        raise WorkspaceConfigError(str(exc)) from exc
    try:
        config = config_from_mapping(
            content,
            anchor=path.parent,
            bundle_root=layout.bundle_dir,
            graph_dir=layout.cache_dir,
            declarations_dir=layout.config_dir,
            source=path.name,
        )
    except ConfigError as exc:
        raise WorkspaceConfigError(str(exc)) from exc
    # `_workspace` names the workspace's own repository in `repo_stamps`;
    # a declared repository may not shadow it.
    if any(entry.name == "_workspace" for entry in config.repos):
        raise WorkspaceConfigError(f"{path}: repository name '_workspace' is reserved for the workspace itself")
    return config


def declared_checkouts(layout: WorkspaceLayout) -> dict[str, Path]:
    """Read declared working checkouts from the layered workspace manifest.

    Local configuration overrides the shared manifest. Relative checkouts
    resolve against the manifest directory, as repository paths do.
    """
    path = layout.manifest_path
    if not path.exists():
        return {}
    try:
        content = workspace_store(layout).read_explicit()
    except StoreValidationError as exc:
        raise WorkspaceConfigError(str(exc)) from exc
    repositories = content.get("repositories")
    if not isinstance(repositories, Mapping):
        return {}
    found: dict[str, Path] = {}
    for name, entry in repositories.items():
        raw = entry.get("checkout") if isinstance(entry, Mapping) else None
        if isinstance(raw, str) and raw.strip():
            expanded = Path(raw).expanduser()
            found[str(name)] = expanded if expanded.is_absolute() else (path.parent / expanded).resolve()
    return found


__all__ = ["WorkspaceConfig", "declared_checkouts", "load_workspace_config"]

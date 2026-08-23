"""Load the declarations/configuration owned by a workspace layout.

Interface consumers depend on this adapter rather than reaching through core
to ``code_wiki_okf.config``. Schema/configuration failures are translated to
the workspace error taxonomy; filesystem failures remain ``OSError``.
"""

from __future__ import annotations

from code_wiki_okf.config import Config, ConfigError, load_config

from graph_works_core.workspace.errors import WorkspaceConfigError
from graph_works_core.workspace.layout import WorkspaceLayout

WorkspaceConfig = Config


def load_workspace_config(layout: WorkspaceLayout) -> WorkspaceConfig:
    """Load the workspace's declarations with layout-derived paths."""
    try:
        return load_config(
            layout.bundle_dir,
            config_path=layout.manifest_path,
            graph_dir=layout.cache_dir,
            declarations_dir=layout.config_dir,
        )
    except ConfigError as exc:
        raise WorkspaceConfigError(str(exc)) from exc


__all__ = ["WorkspaceConfig", "load_workspace_config"]

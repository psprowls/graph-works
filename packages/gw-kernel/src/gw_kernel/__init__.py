"""gw-kernel: workspace discovery, layout, manifest and initialization.

Workspace primitives sit below the shared graph and read_session substrate,
guidance claims and closure, and the events and wiki_stats verticals.
Application slices receive these resolved primitives; kernel never imports them.

The lazy front door exposes the retained workspace and graph APIs. Its export
map, TYPE_CHECKING declarations and __all__ agree, as tested independently.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__version__ = "0.7.0"

_EXPORTS: dict[str, tuple[str, str]] = {
    "GraphResult": ("gw_kernel.graph.commands", "GraphResult"),
    "GraphTarget": ("gw_kernel.graph.commands", "GraphTarget"),
    "graph_target": ("gw_kernel.graph.commands", "graph_target"),
    "HubEntry": ("gw_kernel.wiki_stats.commands", "HubEntry"),
    "WikiStats": ("gw_kernel.wiki_stats.commands", "WikiStats"),
    "compute_stats": ("gw_kernel.wiki_stats.commands", "compute_stats"),
    "find_repo_root": ("gw_kernel.workspace.discovery", "find_repo_root"),
    "resolve": ("gw_kernel.workspace.discovery", "resolve"),
    "InitError": ("gw_kernel.workspace.errors", "InitError"),
    "QueryError": ("gw_kernel.workspace.errors", "QueryError"),
    "ScanError": ("gw_kernel.workspace.errors", "ScanError"),
    "WorkspaceError": ("gw_kernel.workspace.errors", "WorkspaceError"),
    "WorkspaceNotFound": ("gw_kernel.workspace.errors", "WorkspaceNotFound"),
    "INSTALLERS": ("gw_kernel.workspace.init", "INSTALLERS"),
    "Installer": ("gw_kernel.workspace.init", "Installer"),
    "InstallResult": ("gw_kernel.workspace.init", "InstallResult"),
    "PlannedWrite": ("gw_kernel.workspace.init", "PlannedWrite"),
    "WorkspaceInit": ("gw_kernel.workspace.init", "WorkspaceInit"),
    "WorkspacePlan": ("gw_kernel.workspace.init", "WorkspacePlan"),
    "apply_init": ("gw_kernel.workspace.init", "apply_init"),
    "plan_init": ("gw_kernel.workspace.init", "plan_init"),
    "DEFAULT_WORKSPACE_NAME": ("gw_kernel.workspace.layout", "DEFAULT_WORKSPACE_NAME"),
    "MANIFEST_FILENAME": ("gw_kernel.workspace.layout", "MANIFEST_FILENAME"),
    "WorkspaceLayout": ("gw_kernel.workspace.layout", "WorkspaceLayout"),
    "layout_for": ("gw_kernel.workspace.layout", "layout_for"),
    "CATALOG": ("gw_kernel.workspace.manifest", "CATALOG"),
    "MANIFEST_VERSION": ("gw_kernel.workspace.manifest", "MANIFEST_VERSION"),
    "WORKSPACE_DIR_ENV": ("gw_kernel.workspace.manifest", "WORKSPACE_DIR_ENV"),
    "Manifest": ("gw_kernel.workspace.manifest", "Manifest"),
    "PackagedRule": ("gw_kernel.workspace.pipeline", "PackagedRule"),
}

if TYPE_CHECKING:
    from gw_kernel.graph.commands import GraphResult, GraphTarget, graph_target
    from gw_kernel.wiki_stats.commands import HubEntry, WikiStats, compute_stats
    from gw_kernel.workspace.discovery import find_repo_root, resolve
    from gw_kernel.workspace.errors import InitError, QueryError, ScanError, WorkspaceError, WorkspaceNotFound
    from gw_kernel.workspace.init import (
        INSTALLERS,
        Installer,
        InstallResult,
        PlannedWrite,
        WorkspaceInit,
        WorkspacePlan,
        apply_init,
        plan_init,
    )
    from gw_kernel.workspace.layout import (
        DEFAULT_WORKSPACE_NAME,
        MANIFEST_FILENAME,
        WorkspaceLayout,
        layout_for,
    )
    from gw_kernel.workspace.manifest import CATALOG, MANIFEST_VERSION, WORKSPACE_DIR_ENV, Manifest
    from gw_kernel.workspace.pipeline import PackagedRule

__all__ = [
    "CATALOG",
    "DEFAULT_WORKSPACE_NAME",
    "INSTALLERS",
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "WORKSPACE_DIR_ENV",
    "GraphResult",
    "GraphTarget",
    "HubEntry",
    "InitError",
    "InstallResult",
    "Installer",
    "Manifest",
    "PackagedRule",
    "PlannedWrite",
    "QueryError",
    "ScanError",
    "WikiStats",
    "WorkspaceError",
    "WorkspaceInit",
    "WorkspaceLayout",
    "WorkspaceNotFound",
    "WorkspacePlan",
    "__version__",
    "apply_init",
    "compute_stats",
    "find_repo_root",
    "graph_target",
    "layout_for",
    "plan_init",
    "resolve",
]


def __getattr__(name: str) -> Any:  # noqa: ANN401
    """Resolve a public name on first access, then cache it in the module globals."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

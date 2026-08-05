"""Workspace-relative paths owned by code-graph-io.

A workspace is a directory the caller chooses; everything this package writes
lives under one subdirectory of it. Callers pass the workspace in — nothing
here discovers it, reads the environment, or looks for a manifest.
"""

from __future__ import annotations

from pathlib import Path

#: Local machine state only — graph DB and caches. `update` gitignores it
#: wholesale, so it is safe for this directory to sit inside a git repo.
GRAPH_DIRNAME = ".agent-workspace"


def graph_dir(workspace: Path) -> Path:
    """The directory holding the code graph for *workspace*."""
    return Path(workspace) / GRAPH_DIRNAME

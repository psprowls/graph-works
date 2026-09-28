"""The workspace's own git repository as a placement and finish target."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from graph_works_core.workspace.commits import commit_mode
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.provenance import run_git
from graph_works_core.workspace.repos import ItemRepo

WORKSPACE_REPO: Final = "_workspace"


def workspace_repo(layout: WorkspaceLayout) -> tuple[ItemRepo | None, str | None]:
    """The workspace repository when placement is enabled, else a note."""
    root = layout.root.resolve()
    toplevel = run_git(root, "rev-parse", "--show-toplevel")
    if toplevel is None or Path(toplevel.strip()).resolve() != root:
        return None, f"{root}: not the toplevel of its own git repository; workspace placement disabled"
    if commit_mode(layout) == "off":
        return None, "workflow.workspace_commits is off; workspace placement disabled"
    return ItemRepo(WORKSPACE_REPO, root, "workspace"), None


__all__ = ["WORKSPACE_REPO", "workspace_repo"]

"""The workspace's `.gw/sections` declarations, read once per call."""

from __future__ import annotations

from okf_ext.bundle import SECTIONS_DIRNAME
from okf_ext.shape import SectionError, SectionSet, load_sections

from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout


def load_declarations(layout: WorkspaceLayout) -> SectionSet:
    """Every section declaration under the layout's config dir.

    A malformed declaration is workspace configuration, so it raises
    `WorkspaceError` rather than reading as "nothing declared".
    """
    try:
        return load_sections(layout.config_dir / SECTIONS_DIRNAME)
    except SectionError as exc:
        raise WorkspaceError(str(exc)) from exc

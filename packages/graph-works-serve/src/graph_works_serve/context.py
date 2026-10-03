"""Framework-neutral request context and reply. No Starlette here."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from graph_works_core.workspace import discovery
from graph_works_core.workspace.layout import WorkspaceLayout

from graph_works_serve.readstate import ReadState


@dataclass(frozen=True, slots=True)
class Reply:
    """A status and a plain-JSON body; `app.py` turns it into a response."""

    status: int
    body: object
    generation: int | None = None


@dataclass(frozen=True, slots=True)
class ServeContext:
    """What every handler knows: the fixed workspace root and this process's identity."""

    root: Path
    cwd: Path
    port: int
    pid: int
    gw_version: str
    read_state: ReadState = field(default_factory=ReadState, compare=False)

    def layout(self) -> WorkspaceLayout:
        """Re-derive the layout from the fixed root, so a `workspace.yaml` edit needs no restart."""
        return discovery.resolve(workspace=self.root, cwd=self.cwd, environ=os.environ)

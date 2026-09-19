"""`--workspace` resolution (D-002) — the first call every sub-app makes.

All precedence and discovery logic stays in core's `resolve()` (constraint 4: workspace knowledge
flows inward, never outward); this module only forwards and maps the one refusal core can raise to
this CLI's exit-code contract.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from graph_works_core.workspace import discovery
from graph_works_core.workspace.errors import WorkspaceNotFound
from graph_works_core.workspace.layout import WorkspaceLayout

from graph_works_cli import exit_codes
from graph_works_cli.errors import fail


def resolve_workspace(workspace: str, *, json_mode: bool = False, command: str = "") -> WorkspaceLayout:
    """--workspace -> GRAPH_WORKS_DIR env -> cwd git walk-up, via core's resolve().

    Every sub-app declares `--workspace` identically and calls this first. Prints to stderr and
    exits `NOT_INITIALIZED` when no workspace resolves at the target root.  The
    explicit-mode archive/proposal callbacks opt into a JSON refusal envelope;
    all existing callers retain the historic human-only failure by default.
    """
    try:
        return discovery.resolve(workspace=workspace or None, cwd=Path.cwd(), environ=os.environ)
    except WorkspaceNotFound as exc:
        if json_mode:
            fail(
                str(exc),
                reason="workspace",
                json_mode=True,
                command=command,
                code=exit_codes.NOT_INITIALIZED,
                cause=exc,
            )
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=exit_codes.NOT_INITIALIZED) from exc

"""`gw util surface-page` — regenerate the generated sections of `docs/reference/gw-cli-surface.md`.

Typer stays here: this layer walks the live command tree into plain data (registration order,
via `walk_registered`) and hands it to gw-wiki to render and reconcile. Without `--apply` the
verb is the staleness check and writes nothing; it exits `STALE` when the page differs. Like
`gw scan`, it does not commit -- the page is committed by pathspec.
"""

from __future__ import annotations

import importlib.metadata
from datetime import UTC, datetime
from typing import cast

import typer
from gw_wiki.util.surface_page import apply_surface_page, plan_surface_page, render_sections
from gw_wire.util import surface_page_payload

from gw_cli import exit_codes
from gw_cli.errors import exit_error
from gw_cli.introspection import TyperCommand, command_to_help_entry
from gw_cli.json_output import encode
from gw_cli.util_cli.describe import walk_registered
from gw_cli.workspace_resolution import resolve_workspace


def surface_page_sections() -> str:
    """The generated region for the running CLI."""
    from gw_cli.cli import app  # local: cli imports util_cli, so this closes a cycle

    root = cast(TyperCommand, typer.main.get_command(app))
    return render_sections(
        command_to_help_entry(root, name="gw"),
        walk_registered(root),
        importlib.metadata.version("gw-cli"),
    )


def surface_page(
    apply: bool = typer.Option(False, "--apply", help="Write the page when it is stale; without it, only report."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root; defaults to discovery."),
    json_output: bool = typer.Option(False, "--json", help="Emit the plan as JSON."),
) -> None:
    """Report (or with --apply, fix) drift in the generated gw CLI surface reference page."""
    layout = resolve_workspace(workspace)
    plan = plan_surface_page(layout, surface_page_sections(), today=datetime.now(UTC).date())
    try:
        applied = apply_surface_page(plan) if apply else False
    except OSError as exc:
        exit_error(str(exc), cause=exc)

    if json_output:
        typer.echo(encode(surface_page_payload(plan, applied=applied)))
    elif plan.status == "refused":
        typer.echo(f"refused: {plan.detail}")
    elif plan.status == "current":
        typer.echo(f"current: {plan.path}")
    elif applied:
        typer.echo(f"wrote {plan.path} (+{plan.added} -{plan.removed} lines)")
    else:
        typer.echo(f"stale: {plan.path} (+{plan.added} -{plan.removed} lines); run with --apply")

    if plan.status == "refused" or (plan.status == "stale" and not applied):
        raise typer.Exit(code=exit_codes.GENERIC)

"""`gw wiki claims` — refresh, show, closure.

ADR 2026-08-13-command-modules: this module routes, parses, formats, and traces. The index, the
closure and item resolution belong to `graph_works_core.guidance` and are not restated here.
"""

from __future__ import annotations

import typer
from graph_works_core.guidance.claims import ClaimRow
from graph_works_core.guidance.commands import run_claims_closure, run_claims_refresh, run_claims_show
from graph_works_core.workspace.errors import WorkspaceConfigError
from graph_works_wire.wiki import claims_closure_payload, claims_refresh_payload, claims_rows_payload

from graph_works_cli import exit_codes
from graph_works_cli.errors import fail
from graph_works_cli.json_output import encode
from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.workspace_resolution import resolve_workspace

claims_app = typer.Typer(
    name="claims", help="The claims index and a work item's affects closure.", no_args_is_help=True
)


def _row_line(row: ClaimRow) -> str:
    marker = " [superseded]" if row.superseded else ""
    return f"{row.page}#{row.id}  {row.claim}{marker}"


@claims_app.command(name="refresh")
def refresh_command(
    force: bool = typer.Option(False, "--force", help="Rebuild even when the index is fresh."),
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Bring the claims index up to date with the bundle."""
    layout = resolve_workspace(workspace)
    try:
        result = run_claims_refresh(layout, force=force)
    except OSError as exc:
        exit_error(str(exc), cause=exc)
    if json_output:
        typer.echo(encode(claims_refresh_payload(result)))
        return
    state = f"rebuilt ({result.reason})" if result.rebuilt else "fresh"
    typer.echo(f"claims index {state}: {result.pages} pages, {result.extracted} extracted, {result.pruned} pruned")
    for skipped in result.skipped:
        typer.echo(f"skipped {skipped.page} {skipped.key}[{skipped.index}]: {skipped.reason}", err=True)


@claims_app.command(name="show")
def show_command(
    uri: str = typer.Argument(..., metavar="URI"),
    include_superseded: bool = typer.Option(False, "--include-superseded"),
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Rows whose `about` contains exactly URI."""
    layout = resolve_workspace(workspace)
    try:
        result = run_claims_show(layout, uri, include_superseded=include_superseded)
    except OSError as exc:
        exit_error(str(exc), cause=exc)
    if json_output:
        typer.echo(encode(claims_rows_payload(result)))
        return
    if not result.rows:
        typer.echo(f"no claims about {uri}")
        return
    for row in result.rows:
        typer.echo(_row_line(row))


@claims_app.command(name="closure")
def closure_command(
    path: str = typer.Argument(..., metavar="WORK_PATH"),
    include_superseded: bool = typer.Option(False, "--include-superseded"),
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """A work item's affects closure, then the rows it admits in tier order. Uncapped."""
    layout = resolve_workspace(workspace)
    try:
        run = run_claims_closure(layout, path, include_superseded=include_superseded)
    except WorkspaceConfigError as exc:
        exit_error(str(exc), code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        exit_error(str(exc), cause=exc)
    if run.refusal is not None:
        # The work verbs' unknown-path exit: reason "unresolved", exit AMBIGUOUS, envelope under --json.
        fail(
            f"{path}: {run.refusal}" + (f" — {run.detail}" if run.detail else ""),
            reason="unresolved",
            json_mode=json_output,
            command="wiki claims closure",
            code=exit_codes.AMBIGUOUS,
        )
    if json_output:
        typer.echo(encode(claims_closure_payload(run)))
        return
    for warning in run.closure.warnings:
        typer.echo(f"warning: {warning}", err=True)
    for entry in run.closure.entries:
        typer.echo(f"tier {entry.tier}  {entry.uri}  ({entry.why})")
    current: int | None = None
    for matched in run.matched:
        if matched.tier != current:
            current = matched.tier
            typer.echo(f"\n# tier {current}")
        typer.echo(f"{_row_line(matched.row)}  [{matched.row.tokens} tokens]")
    typer.echo(f"\n{len(run.matched)} rows, {run.total_tokens} tokens")

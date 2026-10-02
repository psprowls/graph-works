"""`gw wiki section write`: replace one prose-owned `##` section of a wiki page."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
from graph_works_core.wiki_page import run_section_write
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_wire.wiki import section_write_payload

from graph_works_cli.errors import fail
from graph_works_cli.json_output import encode
from graph_works_cli.workspace_resolution import resolve_workspace

section_app = typer.Typer(name="section", help="Write one prose section of a wiki page.", no_args_is_help=True)


@section_app.command(name="write")
def write(
    page_id: str = typer.Argument(..., help="Extensionless bundle-relative page id."),
    heading: str = typer.Option(..., "--heading", help="The `##` heading text, without the hashes."),
    body_file: str = typer.Option("", "--body-file", help="Read the section body from this file; default stdin."),
    apply: bool = typer.Option(False, "--apply", help="Write the section; without it, only plan."),
    json_output: bool = typer.Option(False, "--json", help="Print the section-write projection instead of text."),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Replace one existing, prose-owned `##` section's body. Plans unless --apply."""
    command = "wiki section write"
    layout = resolve_workspace(workspace, json_mode=json_output, command=command)
    try:
        body = Path(body_file).read_text(encoding="utf-8") if body_file else sys.stdin.read()
        run = run_section_write(layout, page_id, heading, body, today=datetime.now(UTC).date(), dry_run=not apply)
    except WorkspaceError as exc:
        fail(str(exc), reason="workspace", json_mode=json_output, command=command, cause=exc)
    except OSError as exc:
        fail(str(exc), reason="io", json_mode=json_output, command=command, cause=exc)
    payload = section_write_payload(run)
    if run.refusal is not None:
        reason = "unresolved" if run.refusal == "unknown-page" else "refused"
        fail(
            f"{run.refusal}: {page_id} ## {run.heading}",
            reason=reason,
            json_mode=json_output,
            command=command,
            payload=payload,
        )
    if run.failures:
        fail(
            "section write was incomplete",
            reason="incomplete-apply",
            json_mode=json_output,
            command=command,
            payload=payload,
        )
    if json_output:
        typer.echo(encode(payload))
    else:
        typer.echo(f"{'wrote' if run.applied else 'would write'} {page_id}.md ## {run.heading}")

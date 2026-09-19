"""`gw util log` — one timestamped entry appended to the wiki's `log.md`.

The op vocabulary is core's to police: `run_log` raises `ValueError` before touching disk
for an unknown op, and this turns that into a non-zero exit through the shared helper.
`today=` is supplied here because nothing below this layer reads the clock.
"""

from __future__ import annotations

from datetime import UTC, datetime

import typer
from graph_works_core.util.commands import run_log
from graph_works_wire.util import log_payload

from graph_works_cli.errors import exit_error
from graph_works_cli.json_output import encode
from graph_works_cli.workspace_resolution import resolve_workspace


def log(
    op: str = typer.Option(..., "--op", help="One of scan/ingest/query/lint/create/update/delete/note."),
    title: str = typer.Option(..., "--title", help="The entry's headline."),
    detail: str = typer.Option("", "--detail", help="Optional trailing detail."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root; defaults to discovery."),
    json_output: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Append one timestamped entry to the wiki log."""
    layout = resolve_workspace(workspace)
    try:
        result = run_log(layout, op, title, detail or None, today=datetime.now(UTC).date())
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    if json_output:
        typer.echo(encode(log_payload(result)))
    elif result.written:
        typer.echo(result.entry)

    if not result.written:
        exit_error("log entry was not written")

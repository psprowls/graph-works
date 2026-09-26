"""`gw archive` — the combined work-item + wiki sweep (D-031).

`gw wiki archive` shares `run_archive` with a work-free selection.
"""

from __future__ import annotations

from datetime import UTC, datetime

import typer
from graph_works_core.archive.commands import run_archive, stranded_warnings
from graph_works_wire.work import archive_payload

from graph_works_cli.errors import fail
from graph_works_cli.json_output import encode
from graph_works_cli.workspace_resolution import resolve_workspace

COMMAND = "archive"


def archive(
    dry_run: bool = typer.Option(False, "--dry-run", help="Render both plans and write nothing."),
    json_output: bool = typer.Option(
        False, "--json", help="Print the archive projection (graph-works-wire) instead of text."
    ),
    workspace: str = typer.Option("", "--workspace", help="Workspace root; defaults to discovery."),
) -> None:
    """Archive every eligible work item, wiki proposal and drained Source in one sweep."""
    layout = resolve_workspace(workspace, json_mode=json_output, command=COMMAND)
    try:
        run = run_archive(layout, None, None, today=datetime.now(UTC).date(), dry_run=dry_run)
    except (OSError, ValueError) as exc:
        fail(str(exc), reason="io", json_mode=json_output, command=COMMAND, cause=exc)

    for warning in stranded_warnings(run):
        typer.echo(warning, err=True)

    payload = archive_payload(run, dry_run=dry_run)

    if not json_output and (run.conflict or not run.ok or dry_run):
        typer.echo(
            "\n".join(
                [
                    *(f"{source} -> {destination}" for source, destination in run.plan.path_mapping.items()),
                    *(f"! {item.path}: {item.kind} -- {item.detail}" for item in run.plan.refusals),
                ]
            )
        )
        typer.echo(run.wiki_plan.diff())
    if run.conflict:
        fail(
            f"archive refused: the two lanes both touch {', '.join(run.conflict)}",
            reason="conflict",
            json_mode=json_output,
            command=COMMAND,
            payload=payload,
        )
    if not run.ok:
        fail("archive plan was refused", reason="refused", json_mode=json_output, command=COMMAND, payload=payload)
    if run.result is not None and not run.result.ok:
        fail(
            "archive apply was incomplete: " + "; ".join(run.result.failures),
            reason="incomplete-apply",
            json_mode=json_output,
            command=COMMAND,
            payload=payload,
        )
    if run.wiki is not None and not run.wiki.ok:
        details = [
            *(f"{item.path}: {item.kind} -- {item.detail}" for item in run.wiki.refusals),
            *(f"{item.path}: {item.kind} -- {item.error}" for item in run.wiki.move.failed),
        ]
        fail(
            "wiki archive apply was incomplete: " + "; ".join(details),
            reason="incomplete-apply",
            json_mode=json_output,
            command=COMMAND,
            payload=payload,
        )
    if json_output:
        typer.echo(encode(payload))
        return
    if dry_run:
        return

    archived = [
        *(run.plan.path_mapping.values() if run.result is not None else ()),
        *(run.wiki.archived if run.wiki is not None else ()),
    ]
    if archived:
        for token in archived:
            typer.echo(token)
    else:
        typer.echo("nothing to do")

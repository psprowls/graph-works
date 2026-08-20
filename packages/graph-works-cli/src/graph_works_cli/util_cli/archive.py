"""`gw archive` — the combined work-item + wiki sweep (D-031).

`gw wiki archive` calls `doc_wiki_okf.archive` directly, bypassing core's composition, so
this is the only caller of `run_archive` — and therefore the only verb that exposes its
cross-lane conflict guard and its active-work pointer clear.
"""

from __future__ import annotations

from datetime import UTC, datetime

import typer
from graph_works_core.archive.commands import run_archive

from graph_works_cli.errors import exit_error
from graph_works_cli.workspace_resolution import resolve_workspace


def archive(
    dry_run: bool = typer.Option(False, "--dry-run", help="Render both plans and write nothing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root; defaults to discovery."),
) -> None:
    """Archive every eligible work item and every eligible wiki proposal in one sweep."""
    layout = resolve_workspace(workspace)
    try:
        run = run_archive(layout, None, None, today=datetime.now(UTC).date(), dry_run=dry_run)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    if run.conflict or not run.ok or dry_run:
        typer.echo(run.plan.diff())
        typer.echo(run.wiki_plan.diff())
    if run.conflict:
        exit_error(f"archive refused: the two lanes both touch {', '.join(run.conflict)}")
    if not run.ok:
        exit_error("archive plan was refused")
    if dry_run:
        return

    archived = [
        *(run.result.archived if run.result is not None else ()),
        *(run.wiki.archived if run.wiki is not None else ()),
    ]
    if archived:
        for token in archived:
            typer.echo(token)
    else:
        typer.echo("nothing to do")

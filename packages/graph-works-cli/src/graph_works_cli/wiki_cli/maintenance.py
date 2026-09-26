"""`gw wiki` maintenance commands."""

from __future__ import annotations

from datetime import UTC, datetime

import typer
from graph_works_core.archive.commands import run_archive, stranded_warnings
from graph_works_core.wiki_stats.commands import compute_stats
from graph_works_wire.wiki import stats_payload
from graph_works_wire.work import archive_payload
from okf_io import load_bundle, update_index

from graph_works_cli.errors import fail
from graph_works_cli.json_output import encode
from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.workspace_resolution import resolve_workspace


def _render_stats(stats: dict[str, object]) -> str:
    """Keep the human presentation compact while JSON remains the stable schema."""
    lines = [
        f"Pages: {stats['total_pages']}",
        f"Edges: {stats['total_edges']}",
        f"Components: {stats['component_count']}",
    ]
    for label, key in (("Outbound hubs", "top_outbound_hubs"), ("Inbound hubs", "top_inbound_hubs")):
        hubs = stats[key]
        assert isinstance(hubs, list)
        formatted = ", ".join(f"{hub['page']} ({hub['degree']})" for hub in hubs if isinstance(hub, dict))
        lines.append(f"{label}: {formatted or 'none'}")
    for label, key in (("Orphans", "orphans"), ("Sinks", "sinks")):
        members = stats[key]
        assert isinstance(members, list)
        lines.append(f"{label}: {', '.join(str(member) for member in members) or 'none'}")
    return "\n".join(lines)


def stats(
    top: int = typer.Option(10, "--top", min=1),
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Compute deterministic wiki link-graph statistics."""
    layout = resolve_workspace(workspace)
    try:
        result = compute_stats(load_bundle(layout.bundle_dir), top=top)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    payload = stats_payload(result)
    if json_output:
        typer.echo(encode(payload))
    else:
        typer.echo(_render_stats(payload))


def index(workspace: str = typer.Option("", "--workspace")) -> None:
    """Reconcile every represented index and create missing index files."""
    layout = resolve_workspace(workspace)
    try:
        updates = update_index(load_bundle(layout.bundle_dir), directories=None, create_missing=True, dry_run=False)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    changed = [update.path for update in updates if update.changed]
    if changed:
        for path in changed:
            typer.echo(path)
    else:
        typer.echo("nothing to do")


def archive(
    target: str | None = typer.Argument(None),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json", help="Print the archive projection instead of text."),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Archive one curated page, or sweep eligible proposals and drained Sources."""
    command = "wiki archive"
    layout = resolve_workspace(workspace, json_mode=json_output, command=command)
    try:
        run = run_archive(
            layout, (), [target] if target is not None else None, today=datetime.now(UTC).date(), dry_run=dry_run
        )
    except (OSError, ValueError) as exc:
        fail(str(exc), reason="io", json_mode=json_output, command=command, cause=exc)

    for warning in stranded_warnings(run):
        typer.echo(warning, err=True)
    payload = archive_payload(run, dry_run=dry_run)

    if dry_run and not json_output:
        typer.echo(run.wiki_plan.diff())
    if not run.ok:
        fail("archive plan was refused", reason="refused", json_mode=json_output, command=command, payload=payload)
    if not dry_run and ((run.result is not None and not run.result.ok) or (run.wiki is not None and not run.wiki.ok)):
        fail(
            "archive was incomplete",
            reason="incomplete-apply",
            json_mode=json_output,
            command=command,
            payload=payload,
        )
    if json_output:
        typer.echo(encode(payload))
        return
    if dry_run:
        return
    archived = run.wiki.archived if run.wiki is not None else ()
    if archived:
        for token in archived:
            typer.echo(token)
    else:
        typer.echo("nothing to do")

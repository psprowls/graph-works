"""`gw wiki` maintenance commands."""

from __future__ import annotations

import json

import typer
from doc_wiki_okf.archive import ARCHIVE_IGNORE, apply_archive, plan_archive
from graph_works_core.wiki_stats.commands import compute_stats
from okf_io import load_bundle, update_index

from graph_works_cli import exit_codes
from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.wiki_cli.rendering import stats_payload
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
        typer.echo(json.dumps(payload, indent=2))
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
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Archive one curated page or sweep eligible proposal pages."""
    layout = resolve_workspace(workspace)
    try:
        bundle = load_bundle(layout.bundle_dir, ignore=ARCHIVE_IGNORE)
        plan = plan_archive(bundle, [target] if target is not None else None)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    if dry_run:
        typer.echo(plan.diff())
        if not plan.ok:
            exit_error("archive plan was refused")
        return
    if not plan.ok:
        exit_error("archive plan was refused")

    try:
        result = apply_archive(bundle, plan)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)
    if not result.ok:
        exit_error("archive was incomplete", code=exit_codes.GENERIC)

    if result.archived:
        for token in result.archived:
            typer.echo(token)
    else:
        typer.echo("nothing to do")

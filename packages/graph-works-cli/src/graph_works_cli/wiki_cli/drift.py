"""The `gw wiki drift` command -- claude_code-backend brief, or the full judge+propose pipeline."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import typer
from code_graph_io import GraphNotInitializedError, SchemaMismatchError, open_reader
from code_wiki_okf.config import ConfigError, load_config
from graph_works_core.agent_substrate.roles import role_spec
from graph_works_core.graph import commands as graph_commands
from graph_works_core.lint_drift.propagate_drift import plan_drift_brief, run_propagate_drift
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.repos import resolve_repo
from models_io import ModelsIoError

from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.wiki_cli.rendering import drift_brief_payload, drift_payload
from graph_works_cli.workspace_resolution import resolve_workspace


def drift(
    backend: str = typer.Option(None, "--backend"),
    only: str = typer.Option(None, "--only"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Propose curated-page updates for entities whose code moved.

    Reads the code graph as of the last `gw scan` -- it does not rebuild the
    graph itself. Run `gw scan` first if the graph needs refreshing.
    """
    layout = resolve_workspace(workspace)
    try:
        resolved_backend = role_spec("drift_propagator", layout=layout, backend_override=backend).backend
    except (KeyError, WorkspaceError) as exc:
        exit_error(str(exc), cause=exc)

    try:
        config = load_config(
            layout.bundle_dir,
            config_path=layout.manifest_path,
            graph_dir=layout.cache_dir,
            declarations_dir=layout.config_dir,
        )
    except (ConfigError, OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    try:
        repo_root, _ = resolve_repo(layout)
    except ValueError as exc:
        exit_error(str(exc), cause=exc)

    target = graph_commands.graph_target(layout)
    try:
        reader = open_reader(graph_dir=target.graph_dir)
    except (GraphNotInitializedError, SchemaMismatchError) as exc:
        exit_error(f"cannot open the code graph at {target.graph_dir}: {exc}", cause=exc)

    if resolved_backend == "claude_code":
        if only:
            exit_error("--only is not supported under the claude_code backend")
        try:
            brief = plan_drift_brief(layout, config, reader, repo_root=repo_root)
        except (ModelsIoError, OSError, ValueError) as exc:
            exit_error(str(exc), cause=exc)
        if json_output:
            typer.echo(json.dumps(drift_brief_payload(brief), indent=2))
        else:
            for t in brief.targets:
                typer.echo(
                    f"{t.concept_id} ({len(t.candidates)} changed entit{'y' if len(t.candidates) == 1 else 'ies'})"
                )
        return

    now = datetime.now(UTC)
    try:
        result = asyncio.run(
            run_propagate_drift(layout, config, reader, at=now, repo_root=repo_root, dry_run=dry_run, only=only)
        )
    except (ModelsIoError, OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    if json_output:
        typer.echo(json.dumps(drift_payload(result), indent=2))
    else:
        typer.echo(f"Considered {result.entities_considered}, judged {result.pages_judged}, stale {result.pages_stale}")
    if result.errors:
        for error in result.errors:
            typer.echo(f"Warning: {error}", err=True)

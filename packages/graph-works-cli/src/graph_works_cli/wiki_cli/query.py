"""The root-level text query command."""

from __future__ import annotations

import asyncio
import json

import typer
from graph_works_core.agent_substrate.roles import role_spec
from graph_works_core.query.commands import MAX_TOP_K, MIN_TOP_K, default_embedder, plan_query_brief, run_query
from graph_works_core.workspace.errors import QueryError, WorkspaceError
from models_io import ModelsIoError

from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.wiki_cli.rendering import query_brief_payload, query_payload
from graph_works_cli.workspace_resolution import resolve_workspace


def query(
    query_text: str = typer.Option(..., "--query"),
    limit: int = typer.Option(5, "--limit", min=MIN_TOP_K, max=MAX_TOP_K),
    backend: str = typer.Option(None, "--backend"),
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Answer one text query against the initialized workspace."""
    layout = resolve_workspace(workspace)
    try:
        resolved_backend = role_spec("query_orchestrator", layout=layout, backend_override=backend).backend
    except (KeyError, WorkspaceError) as exc:
        exit_error(str(exc), cause=exc)

    try:
        embedder = default_embedder()
    except (ModelsIoError, OSError) as exc:
        exit_error(str(exc), cause=exc)

    if resolved_backend == "claude_code":
        try:
            brief = plan_query_brief(query_text, layout, embedder=embedder, top_k=limit)
        except (ModelsIoError, OSError, QueryError, ValueError) as exc:
            exit_error(str(exc), cause=exc)
        if json_output:
            typer.echo(json.dumps(query_brief_payload(brief), indent=2))
        else:
            typer.echo(f"Query: {brief.query}")
            for page in brief.top_pages:
                typer.echo(f"- {page.path}")
        return

    try:
        result = asyncio.run(run_query(query_text, layout, embedder=embedder, top_k=limit))
    except (ModelsIoError, OSError, QueryError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    if json_output:
        typer.echo(json.dumps(query_payload(result), indent=2))
    else:
        typer.echo(result.answer)
        typer.echo()
        typer.echo("Citations:")
        for citation in result.citations:
            typer.echo(f"- {citation}")
    if result.fallback_error:
        typer.echo(f"Warning: query fallback: {result.fallback_error}", err=True)

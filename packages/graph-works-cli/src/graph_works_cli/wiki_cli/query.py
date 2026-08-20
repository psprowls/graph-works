"""The root-level text query command."""

from __future__ import annotations

import asyncio

import typer
from graph_works_core.query.commands import MAX_TOP_K, MIN_TOP_K, default_embedder, run_query
from graph_works_core.workspace.errors import QueryError
from models_io import ModelsIoError

from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.workspace_resolution import resolve_workspace


def query(
    query_text: str = typer.Option(..., "--query"),
    limit: int = typer.Option(5, "--limit", min=MIN_TOP_K, max=MAX_TOP_K),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Answer one text query against the initialized workspace."""
    layout = resolve_workspace(workspace)
    try:
        result = asyncio.run(run_query(query_text, layout, embedder=default_embedder(), top_k=limit))
    except (ModelsIoError, OSError, QueryError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    typer.echo(result.answer)
    typer.echo()
    typer.echo("Citations:")
    for citation in result.citations:
        typer.echo(f"- {citation}")
    if result.fallback_error:
        typer.echo(f"Warning: query fallback: {result.fallback_error}", err=True)

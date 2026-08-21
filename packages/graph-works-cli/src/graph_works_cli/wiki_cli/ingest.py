"""The root-level single-source ingest command."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import typer
from code_wiki_okf.config import ConfigError, load_config
from graph_works_core.ingest.commands import run_ingest_source, state_gate_adapter
from models_io import ModelsIoError

from graph_works_cli import exit_codes
from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.wiki_cli.rendering import ingest_payload
from graph_works_cli.workspace_resolution import resolve_workspace


def _emit_human(payload: dict[str, object]) -> None:
    """Print the small, human-facing projection of a completed ingest."""
    typer.echo(f"Page: {payload['page']}")
    typer.echo(f"Copy: {payload['copy']}")
    for proposal in cast(list[dict[str, object]], payload["proposals"]):
        typer.echo(f"Proposal: {proposal['status']} {proposal['target']}")


def _emit_warnings(payload: dict[str, object]) -> None:
    """Keep renderer-derived degradation details off stdout."""
    for warning in cast(list[str], payload["warnings"]):
        typer.echo(f"Warning: {warning}", err=True)


def ingest(
    source: Path = typer.Option(..., "--source"),  # noqa: B008 -- Typer declares CLI options in defaults
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Ingest one source into the initialized workspace."""
    layout = resolve_workspace(workspace)
    try:
        config = load_config(layout.bundle_dir, config_path=layout.repositories_path)
    except (ConfigError, OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)
    if not config.repos:
        exit_error("no repositories are configured", code=exit_codes.NOT_IN_GIT_REPO)

    now = datetime.now(UTC)
    try:
        result = asyncio.run(
            run_ingest_source(
                source,
                layout=layout,
                repo=config.repos[0].path,
                today=now.date(),
                at=now,
                by="agent:graph-works-cli",
                state_gate=state_gate_adapter(config),
            )
        )
    except (ModelsIoError, OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    payload = ingest_payload(result)
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        _emit_human(payload)
    _emit_warnings(payload)
    if not result.ok:
        exit_error("ingest was refused")

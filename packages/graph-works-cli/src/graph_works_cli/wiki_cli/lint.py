"""The fixed `gw wiki lint` pipeline."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import typer
from code_wiki_okf.config import ConfigError, load_config
from graph_works_core.lint_drift.lint import run_lint

from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.wiki_cli.rendering import lint_payload
from graph_works_cli.workspace_resolution import resolve_workspace


def lint(
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Run the workspace's mechanical and semantic lint pipeline."""
    layout = resolve_workspace(workspace)
    try:
        config = load_config(layout.bundle_dir)
    except (ConfigError, OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    today = datetime.now(UTC).date()
    try:
        report = asyncio.run(run_lint(layout, config, today=today, repo_root=layout.repo_root))
    except (OSError, ValueError, RuntimeError) as exc:
        exit_error(str(exc), cause=exc)

    if json_output:
        typer.echo(json.dumps(lint_payload(report), indent=2))
    else:
        typer.echo(report.render())
    if not report.ok:
        exit_error("lint failed")

"""`gw` — the graph-works CLI root and its machine-readable help surface."""

from __future__ import annotations

import importlib.metadata
import json
from typing import Any, cast

import click
import typer

from graph_works_cli.config_cli.main import config_app
from graph_works_cli.graph_cli.main import graph_app
from graph_works_cli.introspection import TyperCommand, command_to_help_entry, json_safe_default
from graph_works_cli.logging_config import configure_verbose_logging
from graph_works_cli.util_cli.main import register_util_root_commands, util_app
from graph_works_cli.wiki_cli.main import register_root_commands, wiki_app
from graph_works_cli.work_cli.main import work_app

# test_cli.py imports these by their old private names and must not be edited; keep as aliases.
_command_to_help_entry = command_to_help_entry
_json_safe_default = json_safe_default

app = typer.Typer(name="gw", help="gw: graph-works CLI.", no_args_is_help=True)


@app.callback()
def _root(
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help=(
            "Stream a live execution log to stderr (-v = INFO, -vv = DEBUG). stderr only — stdout "
            "stays clean, so `gw -v scan ... --json | jq` still works. Independent of a command's "
            "own --quiet."
        ),
    ),
) -> None:
    """gw: graph-works CLI."""
    configure_verbose_logging(verbose)


def _json_help_payload(command_path: tuple[str, ...] = ()) -> dict[str, Any]:
    """Build machine-readable CLI help for the root app or a nested command."""
    root_command = cast(TyperCommand, typer.main.get_command(app))
    current = root_command
    resolved_path: list[str] = []

    for part in command_path:
        if not isinstance(current, typer.core.TyperGroup) or part not in current.commands:
            available = sorted(current.commands) if isinstance(current, typer.core.TyperGroup) else []
            raise click.ClickException(
                f"unknown command path: {' '.join(command_path)}"
                + (f" (available: {', '.join(available)})" if available else "")
            )
        current = cast(TyperCommand, current.commands[part])
        resolved_path.append(part)

    command_name = " ".join([root_command.name or "gw", *resolved_path])
    payload = command_to_help_entry(current, name=command_name)
    payload["schema_version"] = 1
    payload["path"] = resolved_path
    return payload


@app.command(name="help")
def help_command(
    command: list[str] = typer.Argument(  # noqa: B008 -- Typer declares CLI arguments in defaults
        None, help="Optional command path, e.g. graph describe."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable help as JSON."),
) -> None:
    """Show gw help, optionally as machine-readable JSON."""
    command_path = tuple(command or ())
    if json_output:
        try:
            typer.echo(json.dumps(_json_help_payload(command_path), indent=2))
        except click.ClickException as exc:
            typer.echo(json.dumps({"status": "error", "message": exc.message}, indent=2))
            raise typer.Exit(code=2) from exc
        return

    ctx = click.Context(cast(click.Command, typer.main.get_command(app)), info_name="gw")
    if not command_path:
        typer.echo(ctx.get_help())
        return

    try:
        payload = _json_help_payload(command_path)
    except click.ClickException as exc:
        typer.echo(f"Error: {exc.message}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"Usage: {payload['name']} {' '.join(payload['usage'])}".rstrip())
    if payload["help"]:
        typer.echo("")
        typer.echo(payload["help"])


@app.command()
def version() -> None:
    """Print version and exit."""
    v = importlib.metadata.version("graph-works-cli")
    typer.echo(f"gw {v}")


app.add_typer(config_app, name="config")
app.add_typer(graph_app, name="graph")
register_root_commands(app)
register_util_root_commands(app)
app.add_typer(wiki_app, name="wiki")
app.add_typer(work_app, name="work")
app.add_typer(util_app, name="util")


if __name__ == "__main__":  # pragma: no cover -- exercised via the `gw` console script
    app()

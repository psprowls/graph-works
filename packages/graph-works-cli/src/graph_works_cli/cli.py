"""`gw` — the graph-works CLI root and its machine-readable help surface."""

from __future__ import annotations

import importlib.metadata
import io
import json
import sys
from typing import Any, cast

import click
import typer

from graph_works_cli.config_cli.main import config_app
from graph_works_cli.graph_cli.main import graph_app
from graph_works_cli.introspection import TyperCommand, command_to_help_entry, json_safe_default
from graph_works_cli.logging_config import configure_verbose_logging
from graph_works_cli.util_cli.main import register_util_root_commands, util_app
from graph_works_cli.wiki_cli.main import register_root_commands, wiki_app
from graph_works_cli.work_cli import rendering as work_rendering
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
    # Click runs the group callback before `cmd.make_context()` parses the
    # subcommand's own options, so this reset always precedes that command's
    # `json_option()` callback -- including the `gw next` root alias, which
    # reuses `gw work next`'s own callback object and would otherwise be
    # missed by a `work_app`-level callback.
    work_rendering.reset_json_mode()


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


def force_lf_newlines(*streams: object) -> None:
    """Pin the process's own text streams to LF.

    `sys.stdout` is built by the interpreter with `newline=None`, which translates every
    `"\n"` written through it to `os.linesep` -- CRLF on Windows. Every `typer.echo` in
    this package therefore emits CRLF there, including the ~20 `--json` payloads, which
    breaks byte comparison against a golden even though the content is identical. No
    `open()` call is involved, so `scripts/check_text_io_explicit.py` cannot see this
    site; it is fixed once here instead of at every echo.

    The `isinstance` narrowing is load-bearing twice over: `TextIO` has no `reconfigure`
    in typeshed (so `sys.stdout.reconfigure(...)` fails `mypy --strict` on both platform
    arms), and a substituted stream -- pytest capture, `CliRunner`, `pythonw`'s `None` --
    is left untouched rather than crashed on.
    """
    for stream in streams:
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(newline="\n")


def main() -> None:  # pragma: no cover -- crosses a process boundary; see test_stdout_newlines.py
    """The `gw` console script. Configures the process, then hands off to Typer."""
    force_lf_newlines(sys.stdout, sys.stderr)
    app()


if __name__ == "__main__":  # pragma: no cover -- exercised via the `gw` console script
    main()

"""Typer-tree introspection, shared by `gw help --json` and `gw util describe-surface`.

C1 built the per-node help payload inside `cli.py` for the single-node `gw help --json`
view. C6 needs the same payload for *every* node in the tree (§4.1), so it moves here and
both views import it. One introspection implementation, two views of it — the alternative
was a second walker whose payload could drift from help's.
"""

from __future__ import annotations

from typing import Any, cast

import click
import typer

TyperCommand = typer.core.TyperCommand | typer.core.TyperGroup


def json_safe_default(value: object) -> object:
    """Coerce an option default into a JSON-serializable value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [json_safe_default(item) for item in value]
    return str(value)


def command_to_help_entry(command: TyperCommand, *, name: str) -> dict[str, Any]:
    """Return a stable, JSON-serializable help entry for a Typer command."""
    options: list[dict[str, Any]] = []
    arguments: list[dict[str, Any]] = []
    for param in command.params:
        if isinstance(param, typer.core.TyperOption):
            options.append(
                {
                    "name": param.name,
                    "opts": list(param.opts),
                    "secondary_opts": list(param.secondary_opts),
                    "help": param.help or "",
                    "required": param.required,
                    "default": json_safe_default(param.default),
                    "is_flag": param.is_flag,
                }
            )
        elif isinstance(param, typer.core.TyperArgument):
            arguments.append({"name": param.name, "required": param.required, "nargs": param.nargs})

    subcommands: list[dict[str, Any]] = []
    if isinstance(command, typer.core.TyperGroup):
        for sub_name, sub_command in command.commands.items():
            if sub_command.hidden:
                continue
            subcommands.append({"name": sub_name, "help": sub_command.get_short_help_str(limit=10_000)})

    click_command = cast(click.Command, command)
    return {
        "name": name,
        "help": command.help or "",
        "short_help": command.get_short_help_str(limit=10_000),
        "usage": click_command.collect_usage_pieces(click.Context(click_command)),
        "arguments": arguments,
        "options": options,
        "commands": subcommands,
    }

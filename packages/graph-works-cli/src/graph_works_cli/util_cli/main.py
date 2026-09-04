"""`gw util` sub-app and the root-level util command registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import typer
from typer.main import get_command_name
from typer.models import CommandInfo

from graph_works_cli.util_cli.archive import archive
from graph_works_cli.util_cli.describe import describe_surface
from graph_works_cli.util_cli.line_endings import line_endings
from graph_works_cli.util_cli.log import log
from graph_works_cli.util_cli.platform import platform
from graph_works_cli.util_cli.tokens import tokens
from graph_works_cli.util_cli.trace import trace
from graph_works_cli.work_cli.main import work_app

util_app = typer.Typer(
    name="util",
    help="Diagnostics: platform/log/line-endings/tokens/trace and the surface freeze.",
    no_args_is_help=True,
)
util_app.command(name="describe-surface")(describe_surface)
util_app.command(name="line-endings")(line_endings)
util_app.command(name="log")(log)
util_app.command(name="platform")(platform)
util_app.command(name="tokens")(tokens)
util_app.command(name="trace")(trace)


def command_name(command: CommandInfo) -> str:
    """The CLI-visible name of a registered Typer command.

    Typer derives an unnamed command's name from its callback with
    `get_command_name`, so asking Typer rather than guessing keeps this correct
    whichever registration form a command happens to use.
    """
    if command.name is not None:
        return command.name
    assert command.callback is not None
    return get_command_name(command.callback.__name__)


def work_next_callback() -> Callable[..., Any]:
    """`gw work next`'s own callback, so `gw next` is a true alias and not a copy."""
    for command in work_app.registered_commands:
        if command_name(command) == "next":
            assert command.callback is not None
            return command.callback
    raise RuntimeError("gw work next is not registered — `gw next` has nothing to alias")


def register_util_root_commands(app: typer.Typer) -> None:
    """Install the util sub-app's root-level verbs at the ``gw`` root.

    Named distinctly from `wiki_cli.main.register_root_commands` so `cli.py` can import
    both without collision.
    """
    app.command(name="archive")(archive)
    app.command(name="next")(work_next_callback())

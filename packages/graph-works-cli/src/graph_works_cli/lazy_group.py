"""The `gw` root group, which imports a command's module only when that command is asked for.

Every root entry's name, kind and first-paragraph help is static data here, so `gw --help` and
command dispatch touch no command module except the one being run. `tests/test_lazy_root_group.py`
pins each entry against the command it stands in for, so a docstring edit that drifts from the
registry fails a test, not a user.
"""

from __future__ import annotations

import importlib
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import typer
import typer.core
import typer.main

if TYPE_CHECKING:
    # Typer >= 0.27 vendors its own click; annotate against the classes TyperGroup actually uses.
    from typer._click.core import Command as ClickCommand
    from typer._click.core import Context as ClickContext
    from typer._click.formatting import HelpFormatter

Kind = Literal["local", "group", "command", "factory"]


@dataclass(frozen=True)
class RootEntry:
    name: str
    kind: Kind
    help: str
    module: str = ""
    attribute: str = ""


# Today's registration order. `local` entries are defined in cli.py itself; `factory` is a
# callable returning the command function (`next` aliases `work next`'s own callback).
ROOT_REGISTRY: tuple[RootEntry, ...] = (
    RootEntry("help", "local", "Show gw help, optionally as machine-readable JSON."),
    RootEntry("version", "local", "Print version and exit."),
    RootEntry(
        "bootstrap",
        "command",
        "Create a graph-works workspace if it has not been initialized yet.",
        "graph_works_cli.wiki_cli.bootstrap",
        "bootstrap",
    ),
    RootEntry(
        "scan",
        "command",
        "Run scan locally, emit its worklist, or apply an external result directory.",
        "graph_works_cli.wiki_cli.scan",
        "scan",
    ),
    RootEntry(
        "ingest",
        "command",
        "Ingest one source into the initialized workspace.",
        "graph_works_cli.wiki_cli.ingest",
        "ingest",
    ),
    RootEntry(
        "query",
        "command",
        "Answer one text query against the initialized workspace.",
        "graph_works_cli.wiki_cli.query",
        "query",
    ),
    RootEntry(
        "archive",
        "command",
        "Archive every eligible work item, wiki proposal and drained Source in one sweep.",
        "graph_works_cli.util_cli.archive",
        "archive",
    ),
    RootEntry(
        "next",
        "factory",
        "Compute what to dispatch for PATH, and what advancing would change.",
        "graph_works_cli.util_cli.main",
        "work_next_callback",
    ),
    RootEntry(
        "config",
        "group",
        "Read and write graph-works workspace configuration.",
        "graph_works_cli.config_cli.main",
        "config_app",
    ),
    RootEntry(
        "agent-config",
        "group",
        "Read how Claude Code, Codex and Pi are configured for this workspace's projects.",
        "graph_works_cli.agent_config_cli.main",
        "agent_config_app",
    ),
    RootEntry("graph", "group", "Code-graph queries.", "graph_works_cli.graph_cli.main", "graph_app"),
    RootEntry("wiki", "group", "Wiki scan/ingest/query/lint.", "graph_works_cli.wiki_cli.main", "wiki_app"),
    RootEntry("work", "group", "Work-item pipeline verbs.", "graph_works_cli.work_cli.main", "work_app"),
    RootEntry(
        "repo",
        "group",
        "Repository clones pinned in the repositories/ lane, with optional managed working checkouts.",
        "graph_works_cli.repo_cli.main",
        "repo_app",
    ),
    RootEntry(
        "util",
        "group",
        "Diagnostics: platform/log/line-endings/read-index/tokens/trace and the surface freeze.",
        "graph_works_cli.util_cli.main",
        "util_app",
    ),
)

_BY_NAME = {entry.name: entry for entry in ROOT_REGISTRY}


def _build(entry: RootEntry) -> ClickCommand:
    target = getattr(importlib.import_module(entry.module), entry.attribute)
    if entry.kind == "group":
        # `get_group`, not `get_command`: add_typer never gives a sub-app its own completion options.
        return typer.main.get_group(target)
    function = target() if entry.kind == "factory" else target
    single = typer.Typer(add_completion=False)  # a lone command must not grow --install-completion
    single.command(name=entry.name)(function)
    return typer.main.get_command(single)


class LazyRootGroup(typer.core.TyperGroup):
    """A TyperGroup whose registered entries are imported on first use."""

    _complete = False

    @property
    def commands(self) -> MutableMapping[str, ClickCommand]:
        """Every entry, in registry order. Walkers (`help --json`, describe-surface) need all of them."""
        if not self._complete:
            loaded = {e.name: cmd for e in ROOT_REGISTRY if (cmd := self._command(e.name)) is not None}
            self._commands.clear()
            self._commands.update(loaded)
            self._complete = True
        return self._commands

    @commands.setter
    def commands(self, value: MutableMapping[str, ClickCommand]) -> None:
        self._commands = value

    def _command(self, name: str) -> ClickCommand | None:
        if name in self._commands:
            return self._commands[name]
        entry = _BY_NAME.get(name)
        if entry is None or entry.kind == "local":
            return None
        command = self._commands[name] = _build(entry)
        return command

    def list_commands(self, ctx: ClickContext) -> list[str]:
        return [entry.name for entry in ROOT_REGISTRY]

    def get_command(self, ctx: ClickContext, cmd_name: str) -> ClickCommand | None:
        return self._command(cmd_name)

    def format_help(self, ctx: ClickContext, formatter: HelpFormatter) -> None:
        """Render `--help` from registry stubs, so listing the commands imports none of them."""
        shim = typer.core.TyperGroup(
            name=self.name,
            help=self.help,
            epilog=self.epilog,
            params=self.params,
            commands={e.name: typer.core.TyperCommand(e.name, help=e.help) for e in ROOT_REGISTRY},
            rich_markup_mode=self.rich_markup_mode,
            rich_help_panel=self.rich_help_panel,
            subcommand_metavar=self.subcommand_metavar,
            no_args_is_help=self.no_args_is_help,
        )
        typer.core.TyperGroup.format_help(shim, ctx, formatter)

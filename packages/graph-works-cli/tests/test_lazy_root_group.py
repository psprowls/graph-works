"""The lazy root group (D-002): the registry agrees with the commands it stands in for."""

from __future__ import annotations

import json

import click
import pytest
import typer.core
import typer.main
from graph_works_cli import cli
from graph_works_cli.lazy_group import ROOT_REGISTRY, LazyRootGroup, RootEntry
from graph_works_cli.util_cli.main import work_next_callback
from graph_works_cli.work_cli.main import work_app
from typer.testing import CliRunner

runner = CliRunner()
EXPECTED_ORDER = [
    "help", "version", "bootstrap", "scan", "ingest", "query", "archive",
    "next", "config", "agent-config", "graph", "wiki", "work", "util",
]  # fmt: skip


def _eager_root() -> click.Group:
    return typer.main.get_command(cli.app)  # type: ignore[return-value]  # forces the load-all path


def test_the_root_is_a_lazy_group_in_todays_order() -> None:
    root = typer.main.get_command(cli.app)
    assert isinstance(root, LazyRootGroup)
    assert [entry.name for entry in ROOT_REGISTRY] == EXPECTED_ORDER
    assert list(root.commands) == EXPECTED_ORDER
    assert root.list_commands(click.Context(root)) == EXPECTED_ORDER


@pytest.mark.parametrize("entry", ROOT_REGISTRY, ids=lambda entry: entry.name)
def test_each_registry_entry_agrees_with_the_loaded_command(entry: RootEntry) -> None:
    loaded = _eager_root().commands[entry.name]
    assert loaded.name == entry.name
    assert not loaded.hidden and not loaded.deprecated
    assert (loaded.help or "").split("\n\n", 1)[0] == entry.help
    assert isinstance(loaded, typer.core.TyperGroup) == (entry.kind == "group")


def test_get_command_builds_only_the_requested_entry() -> None:
    root = typer.main.get_command(cli.app)
    assert isinstance(root, LazyRootGroup)
    before = set(root._commands)
    assert root.get_command(click.Context(root), "graph") is not None
    assert set(root._commands) - before == {"graph"}


def test_next_is_still_work_nexts_own_callback() -> None:
    nxt = _eager_root().commands["next"]
    assert isinstance(nxt, typer.core.TyperCommand)
    assert nxt.callback is not None
    assert work_next_callback() is next(c.callback for c in work_app.registered_commands if c.name == "next")


def test_help_text_is_byte_identical_to_the_eager_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    lazy = runner.invoke(cli.app, ["--help"])
    assert lazy.exit_code == 0
    for entry in ROOT_REGISTRY:
        assert entry.name in lazy.output
    # The stock renderer over the same app: Typer's own format_help calls get_command for every entry.
    monkeypatch.setattr(LazyRootGroup, "format_help", typer.core.TyperGroup.format_help)
    eager = runner.invoke(cli.app, ["--help"])
    assert eager.exit_code == 0
    assert lazy.output == eager.output


def test_an_unknown_command_is_a_usage_error() -> None:
    result = runner.invoke(cli.app, ["no-such-command"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_the_walkers_see_every_root_entry_once() -> None:
    result = runner.invoke(cli.app, ["help", "--json"])
    assert result.exit_code == 0
    names = [command["name"].split()[-1] for command in json.loads(result.output)["commands"]]
    assert names == EXPECTED_ORDER
    assert len(set(names)) == len(names)

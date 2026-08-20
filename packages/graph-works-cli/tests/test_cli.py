"""`gw --help` / `gw version` / `gw help --json` — CliRunner, no core dependency."""

from __future__ import annotations

import json
from typing import Any, cast

import click
import typer
from graph_works_cli import cli as cli_module
from graph_works_cli.cli import _command_to_help_entry, _json_safe_default, app
from typer.testing import CliRunner

runner = CliRunner()


def test_help_lists_all_five_sub_apps() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for name in ("config", "graph", "wiki", "work", "util"):
        assert name in result.output


def test_version_prints_the_installed_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.output.startswith("gw ")


def test_help_json_describes_the_root_app() -> None:
    result = runner.invoke(app, ["help", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["name"] == "gw"
    assert payload["schema_version"] == 1
    assert payload["path"] == []
    sub_names = {command["name"] for command in payload["commands"]}
    assert {"config", "graph", "wiki", "work", "util"} <= sub_names


def test_help_json_describes_a_sub_app() -> None:
    result = runner.invoke(app, ["help", "graph", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["name"] == "gw graph"
    assert payload["path"] == ["graph"]


def test_help_json_describes_a_command_argument() -> None:
    result = runner.invoke(app, ["help", "config", "get", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["path"] == ["config", "get"]
    assert payload["arguments"] == [{"name": "key", "required": True, "nargs": 1}]


def test_help_json_unknown_command_path_exits_2() -> None:
    result = runner.invoke(app, ["help", "nope", "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert result.stderr == ""


def test_help_text_renders_root_usage() -> None:
    result = runner.invoke(app, ["help"])

    assert result.exit_code == 0
    assert "Usage: gw [OPTIONS] COMMAND [ARGS]..." in result.stdout


def test_root_help_uses_a_json_capable_command_in_its_verbose_example() -> None:
    """A verbose usage example must not recommend an option the command rejects."""
    result = runner.invoke(app, ["--help"])
    help_text = " ".join(result.stdout.replace("│", "").split())

    assert result.exit_code == 0
    assert "gw -v scan ... --json | jq" in help_text
    assert "gw -v query ... --json | jq" not in help_text


def test_help_text_renders_subcommand_usage() -> None:
    result = runner.invoke(app, ["help", "config"])

    assert result.exit_code == 0
    assert "Usage: gw config [OPTIONS] COMMAND [ARGS]..." in result.stdout
    assert "Read and write graph-works workspace configuration." in result.stdout


def test_help_text_unknown_command_path_exits_2() -> None:
    result = runner.invoke(app, ["help", "nope"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Error: unknown command path: nope" in result.stderr


def test_json_safe_default_handles_collections_and_other_objects() -> None:
    unsupported = object()

    assert _json_safe_default([1, ("two", unsupported)]) == [1, ["two", str(unsupported)]]


def test_verbose_flag_configures_logging(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr("graph_works_cli.cli.configure_verbose_logging", calls.append)

    result = runner.invoke(app, ["-v", "version"])

    assert result.exit_code == 0
    assert calls == [1]


def _help_entry_for(command_app: typer.Typer, name: str = "gw demo") -> dict[str, object]:
    """Render one Typer app through the same help projection `gw help --json` uses."""
    return _command_to_help_entry(cast(Any, typer.main.get_command(command_app)), name=name)


def test_help_json_omits_hidden_subcommands() -> None:
    """Hidden commands are unsupported surface; listing them would invite callers to depend on them."""
    demo = typer.Typer()

    @demo.command()
    def shown() -> None:
        """Shown."""

    @demo.command(hidden=True)
    def concealed() -> None:
        """Concealed."""

    entry = _help_entry_for(demo)

    assert [sub["name"] for sub in cast(list[dict[str, object]], entry["commands"])] == ["shown"]


def test_help_json_ignores_a_plain_click_parameter() -> None:
    """Only Typer's own parameter kinds carry the metadata the projection reads; others must be skipped."""
    demo = typer.Typer()

    @demo.command()
    def only(flag: bool = typer.Option(False, "--flag")) -> None:
        """Only."""

    @demo.command()
    def other() -> None:
        """Other."""

    command = cast(Any, typer.main.get_command(demo))
    inner = command.commands["only"]
    inner.params = [*inner.params, click.Option(["--raw"], is_flag=True)]

    entry = _command_to_help_entry(inner, name="gw demo only")

    assert [option["name"] for option in cast(list[dict[str, object]], entry["options"])] == ["flag"]
    assert entry["arguments"] == []


def test_help_text_prints_usage_alone_when_a_command_has_no_help_body(monkeypatch) -> None:
    """A missing docstring must still render a usable usage line, not a blank trailing section."""
    monkeypatch.setattr(
        cli_module,
        "_json_help_payload",
        lambda _path: {"name": "gw demo", "usage": ["[OPTIONS]"], "help": ""},
    )

    result = runner.invoke(app, ["help", "demo"])

    assert result.exit_code == 0
    assert result.stdout == "Usage: gw demo [OPTIONS]\n"

"""`gw util describe-surface` — the machine-readable freeze of the whole CLI tree (D-030).

C1's `gw help --json` describes exactly one node. This walks every node — groups as well
as leaves — so an asserter can check `["wiki", "archive"]` without inferring it from a
parent's `commands` list. Ordering is a plain sort on `path`, so the output is byte-stable
across runs, which is what makes `tests/fixtures/surface.golden.json` mean something.

There is deliberately no `json_keys` field. D-001 specified one; Typer cannot introspect a
command's output shape, so it could only be a hand-maintained registry with nothing
asserting it matches real output — it would rot silently and make the freeze less
trustworthy than no field at all. D-030 amends D-001 rather than dropping it.
"""

from __future__ import annotations

import importlib.metadata
import json
from typing import Any, cast

import typer

from graph_works_cli.introspection import TyperCommand, command_to_help_entry

SCHEMA_VERSION = 1
CLI_NAME = "graph-works-cli"


def _walk(command: TyperCommand, path: tuple[str, ...]) -> list[dict[str, Any]]:
    """Every node at or under *command*. The root (empty path) describes no verb, so it
    is walked into but never emitted."""
    entries: list[dict[str, Any]] = []
    if path:
        entry = command_to_help_entry(command, name=" ".join(("gw", *path)))
        entry["path"] = list(path)
        entry["commands"] = sorted(str(sub["name"]) for sub in entry["commands"])
        entries.append(entry)
    if isinstance(command, typer.core.TyperGroup):
        for name, sub_command in command.commands.items():
            if sub_command.hidden:
                continue
            entries.extend(_walk(cast(TyperCommand, sub_command), (*path, name)))
    return entries


def surface_payload() -> dict[str, Any]:
    """The whole `gw` surface as one JSON-serializable dict."""
    from graph_works_cli.cli import app  # local: cli imports util_cli, so this closes a cycle

    root = cast(TyperCommand, typer.main.get_command(app))
    commands = sorted(_walk(root, ()), key=lambda entry: entry["path"])
    return {
        "schema_version": SCHEMA_VERSION,
        "cli": CLI_NAME,
        "version": importlib.metadata.version("graph-works-cli"),
        "commands": commands,
    }


def describe_surface(
    json_output: bool = typer.Option(False, "--json", help="Emit the whole surface as JSON."),
) -> None:
    """Describe every command in the gw tree — the machine-readable surface freeze."""
    payload = surface_payload()
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    for entry in payload["commands"]:
        typer.echo(" ".join(entry["path"]))

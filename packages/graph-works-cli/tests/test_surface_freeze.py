"""The surface freeze — `describe-surface` against a checked-in golden (§4.1).

Regenerating the golden alone would prove nothing: what holds it honest is
`test_every_reachable_command_appears_exactly_once`, which walks the Typer tree with its
own code rather than the walker under test.
"""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from typing import Any, cast

import typer
from graph_works_cli.cli import app
from graph_works_cli.introspection import TyperCommand
from typer.testing import CliRunner

runner = CliRunner()
GOLDEN = Path(__file__).parent / "fixtures" / "surface.golden.json"


def _payload() -> dict[str, Any]:
    result = runner.invoke(app, ["util", "describe-surface", "--json"])
    assert result.exit_code == 0, result.stdout
    return cast(dict[str, Any], json.loads(result.stdout))


def _reachable_paths(command: TyperCommand, path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """An independent walk of the Typer tree. Deliberately does not import describe._walk:
    comparing the walker against itself would assert nothing.

    IMPORTANT: Use typer.core.TyperGroup for the isinstance check, not click.Group. This
    workspace's typer version does NOT subclass click's types; using click.Group silently returns
    an empty walk. See this package's CLAUDE.md "typer.core vs click" section for the full gotcha.
    """
    found = [path] if path else []
    if isinstance(command, typer.core.TyperGroup):
        for name, sub in command.commands.items():
            if sub.hidden:
                continue
            found.extend(_reachable_paths(cast(TyperCommand, sub), (*path, name)))
    return found


def test_the_surface_matches_the_checked_in_golden() -> None:
    """Adding or changing a verb fails here until the golden moves in the same change."""
    observed = _payload()
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert observed.pop("version") == importlib.metadata.version("graph-works-cli")
    golden.pop("version")
    assert observed == golden


def test_every_reachable_command_appears_exactly_once() -> None:
    root = cast(TyperCommand, typer.main.get_command(app))
    expected = _reachable_paths(root)
    observed = [tuple(entry["path"]) for entry in _payload()["commands"]]

    assert sorted(observed) == sorted(expected)
    assert len(observed) == len(set(observed))


def test_the_command_list_is_sorted_by_path() -> None:
    paths = [entry["path"] for entry in _payload()["commands"]]

    assert paths == sorted(paths)


def test_the_golden_pins_every_verb_this_item_shipped() -> None:
    """A golden regenerated from a half-registered app would freeze the wrong surface."""
    paths = {tuple(entry["path"]) for entry in json.loads(GOLDEN.read_text(encoding="utf-8"))["commands"]}

    assert {
        ("agent-config",),
        ("agent-config", "show"),
        ("archive",),
        ("next",),
        ("util", "describe-surface"),
        ("util", "log"),
        ("util", "tokens"),
        ("util", "trace"),
        ("work", "adopt"),
        ("work", "ingest-queue"),
        ("work", "reparent"),
        ("work", "record-placement"),
    } <= paths

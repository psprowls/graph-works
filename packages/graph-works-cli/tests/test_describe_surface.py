"""`gw util describe-surface` — the whole-tree walker behind the freeze (D-030)."""

from __future__ import annotations

import importlib.metadata
import json

from graph_works_cli.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def _surface() -> dict[str, object]:
    result = runner.invoke(app, ["util", "describe-surface", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def test_the_envelope_names_the_cli_and_its_version() -> None:
    """The freeze is worthless if a consumer cannot tell which binary produced it."""
    payload = _surface()

    assert payload["schema_version"] == 1
    assert payload["cli"] == "graph-works-cli"
    assert payload["version"] == importlib.metadata.version("graph-works-cli")


def test_groups_and_leaves_both_get_their_own_entry() -> None:
    """An asserter must reach ['wiki', 'archive'] without inferring it from a parent."""
    payload = _surface()
    commands = payload["commands"]
    assert isinstance(commands, list)
    paths = [entry["path"] for entry in commands]

    assert ["wiki"] in paths
    assert ["wiki", "archive"] in paths
    assert ["util", "describe-surface"] in paths
    assert [] not in paths


def test_a_group_lists_its_children_as_sorted_names() -> None:
    payload = _surface()
    commands = payload["commands"]
    assert isinstance(commands, list)
    wiki = next(entry for entry in commands if entry["path"] == ["wiki"])
    leaf = next(entry for entry in commands if entry["path"] == ["wiki", "archive"])

    assert wiki["commands"] == sorted(wiki["commands"])
    assert all(isinstance(name, str) for name in wiki["commands"])
    assert "archive" in wiki["commands"]
    assert leaf["commands"] == []


def test_the_payload_is_sorted_by_path_and_stable_across_runs() -> None:
    """Byte stability is what lets the golden in test_surface_freeze.py mean anything."""
    first = runner.invoke(app, ["util", "describe-surface", "--json"])
    second = runner.invoke(app, ["util", "describe-surface", "--json"])

    assert first.stdout == second.stdout
    paths = [entry["path"] for entry in json.loads(first.stdout)["commands"]]
    assert paths == sorted(paths)


def test_no_entry_claims_a_json_keys_field() -> None:
    """D-030 dropped json_keys: Typer cannot introspect output shapes."""
    payload = _surface()
    commands = payload["commands"]
    assert isinstance(commands, list)

    assert all("json_keys" not in entry for entry in commands)


def test_the_human_view_prints_one_path_per_line_in_the_same_order() -> None:
    result = runner.invoke(app, ["util", "describe-surface"])

    assert result.exit_code == 0
    printed = result.stdout.strip().splitlines()
    expected = [" ".join(entry["path"]) for entry in _surface()["commands"]]
    assert printed == expected

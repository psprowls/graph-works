"""Validated config writes and projection refresh behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from config_io import PlainYamlStore, RegistryError
from graph_works_cli import exit_codes
from graph_works_cli.config_cli import main as config_main
from graph_works_cli.config_cli.main import config_app
from typer.testing import CliRunner

runner = CliRunner()


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "workspace.yaml").write_text("version: 1\n", encoding="utf-8")
    return root


def test_set_coerces_persists_and_refreshes_projection(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(
        config_app,
        ["set", "workflow.auto_drive.max_parallel", "3", "--workspace", str(root), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["key"] == "workflow.auto_drive.max_parallel"
    assert payload["value"] == 3
    assert payload["origin"] == "manifest"
    stored = PlainYamlStore(root / "workspace.yaml").read_explicit()
    assert stored["workflow"] == {"auto_drive": {"max_parallel": 3}}
    projection = json.loads((root / "_gw" / "_config" / "config.json").read_text(encoding="utf-8"))
    assert projection["workflow"]["auto_drive"]["max_parallel"] == 3
    assert set(projection["_meta"]) == {"source_mtime", "source_sha256"}


def test_unset_removes_explicit_value_refreshes_projection_and_reports_default(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    runner.invoke(
        config_app,
        ["set", "workflow.auto_drive.max_parallel", "3", "--workspace", str(root)],
    )

    result = runner.invoke(
        config_app,
        ["unset", "workflow.auto_drive.max_parallel", "--workspace", str(root), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["value"] == 2
    assert payload["origin"] == "default"
    stored = PlainYamlStore(root / "workspace.yaml").read_explicit()
    assert "workflow" not in stored
    projection = json.loads((root / "_gw" / "_config" / "config.json").read_text(encoding="utf-8"))
    assert "workflow" not in projection


def test_set_rejects_invalid_values_without_writing(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(
        config_app,
        ["set", "workflow.auto_drive.max_parallel", "many", "--workspace", str(root)],
    )

    assert result.exit_code == exit_codes.GENERIC
    assert "expects an integer" in result.stderr
    assert PlainYamlStore(root / "workspace.yaml").read_explicit() == {"version": 1}
    assert not (root / "_gw" / "_config" / "config.json").exists()


def test_set_rejects_read_only_catalog_keys(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(config_app, ["set", "version", "1", "--workspace", str(root)])

    assert result.exit_code == exit_codes.GENERIC
    assert "declared read-only" in result.stderr


def test_unset_unknown_key_uses_generic_exit(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(config_app, ["unset", "does.not.exist", "--workspace", str(root)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "unknown config key 'does.not.exist'" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["set", "topic", "Demo"],
        ["unset", "topic"],
        ["sync"],
    ],
)
def test_write_verbs_use_schema_mismatch_for_a_malformed_store(tmp_path: Path, arguments: list[str]) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "workspace.yaml").write_text("version: [unclosed\n", encoding="utf-8")

    result = runner.invoke(config_app, [*arguments, "--workspace", str(root)])

    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert result.stdout == ""
    assert "is not valid YAML" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["set", "topic", "Demo"],
        ["unset", "topic"],
        ["sync"],
    ],
)
def test_write_verbs_preserve_not_initialized_for_a_missing_workspace(tmp_path: Path, arguments: list[str]) -> None:
    missing = tmp_path / "missing"

    result = runner.invoke(config_app, [*arguments, "--workspace", str(missing)])

    assert result.exit_code == exit_codes.NOT_INITIALIZED
    assert result.stdout == ""
    assert "no workspace.yaml here" in result.stderr


def test_sync_regenerates_projection_after_a_hand_edit(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    PlainYamlStore(root / "workspace.yaml").write({"version": 1, "topic": "Hand edited"})

    result = runner.invoke(config_app, ["sync", "--workspace", str(root)])

    target = root / "_gw" / "_config" / "config.json"
    assert result.exit_code == 0
    assert result.stdout == f"[ok] projection: {target}\n"
    assert json.loads(target.read_text(encoding="utf-8"))["topic"] == "Hand edited"


def test_sync_json_names_the_projection_path(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(config_app, ["sync", "--workspace", str(root), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"projection": str(root / "_gw" / "_config" / "config.json")}


def test_sync_maps_a_registry_fault_to_the_generic_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A projection that cannot resolve the registry is a tool fault, not a malformed store."""
    root = _workspace(tmp_path)

    def raise_registry_error(*_args: object, **_kwargs: object) -> object:
        raise RegistryError("no projection resolver for 'workflow.*'")

    monkeypatch.setattr(config_main, "write_projection", raise_registry_error)

    result = runner.invoke(config_app, ["sync", "--workspace", str(root)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "Error: no projection resolver for 'workflow.*'" in result.stderr

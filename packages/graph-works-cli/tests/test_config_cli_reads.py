"""Read-only `gw config` verbs against a real temp manifest/catalog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from config_io import ConfigEntry, RegistryError, Resolved
from graph_works_cli import exit_codes
from graph_works_cli.config_cli import main as config_main
from graph_works_cli.config_cli.main import config_app
from graph_works_cli.config_cli.rendering import render_resolved, render_resolved_list
from typer.testing import CliRunner

runner = CliRunner()


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "workspace.yaml").write_text(
        """version: 1
topic: Demo
roles:
  librarian:
    model_id: anthropic.claude-test
""",
        encoding="utf-8",
    )
    return root


def test_get_prints_effective_value_and_origin(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(config_app, ["get", "topic", "--workspace", str(root)])

    assert result.exit_code == 0
    assert result.stdout == "topic = 'Demo'  (origin: manifest)\n"
    assert result.stderr == ""


def test_get_json_is_dataclasses_asdict_shape(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(config_app, ["get", "topic", "--workspace", str(root), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["key"] == "topic"
    assert payload["value"] == "Demo"
    assert payload["origin"] == "manifest"
    assert payload["shadowed"] is None
    assert payload["entry"]["default"] is None
    assert payload["entry"]["description"] == "Display name for this workspace."


def test_list_includes_concrete_wildcard_expansions(tmp_path: Path, monkeypatch) -> None:
    root = _workspace(tmp_path)
    monkeypatch.delenv("GRAPH_WORKS_DIR", raising=False)

    result = runner.invoke(config_app, ["list", "--workspace", str(root), "--json"])

    assert result.exit_code == 0
    rows = {row["key"]: row for row in json.loads(result.stdout)}
    assert rows["topic"]["value"] == "Demo"
    assert rows["roles.librarian.model_id"]["value"] == "anthropic.claude-test"
    assert rows["roles.librarian.model_id"]["origin"] == "manifest"
    assert "roles.*.model_id" not in rows


def test_list_plain_output_documents_default_and_description(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(config_app, ["list", "--workspace", str(root)])

    assert result.exit_code == 0
    assert "+ topic = 'Demo'  [manifest; default None]" in result.stdout
    assert "    Display name for this workspace." in result.stdout


def test_plain_render_notes_an_environment_shadow() -> None:
    result = Resolved(
        key="example",
        value="environment",
        origin="env",
        entry=ConfigEntry(
            key="example",
            type="str",
            default=None,
            description="Example setting.",
            env_var="EXAMPLE_VALUE",
        ),
        shadowed="manifest",
    )

    assert render_resolved(result, json_output=False) == (
        "example = 'environment'  (origin: env)\n  note: manifest value 'manifest' is shadowed by $EXAMPLE_VALUE"
    )


def test_unknown_key_uses_generic_exit(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(config_app, ["get", "does.not.exist", "--workspace", str(root)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "Error: unknown config key 'does.not.exist'" in result.stderr


def test_missing_workspace_uses_not_initialized_exit(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    result = runner.invoke(config_app, ["get", "topic", "--workspace", str(missing)])

    assert result.exit_code == exit_codes.NOT_INITIALIZED
    assert "no workspace.yaml here" in result.stderr


def test_unsupported_manifest_version_uses_schema_mismatch_exit(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "workspace.yaml").write_text("version: 99\n", encoding="utf-8")

    result = runner.invoke(config_app, ["list", "--workspace", str(root)])

    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert "Error:" in result.stderr
    assert "manifest version 99 is not supported" in result.stderr


def test_malformed_manifest_uses_schema_mismatch_exit(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "workspace.yaml").write_text("version: [unclosed\n", encoding="utf-8")

    result = runner.invoke(config_app, ["list", "--workspace", str(root)])

    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert result.stdout == ""
    assert "is not valid YAML" in result.stderr


def test_get_refuses_a_hand_edited_invalid_manifest_value(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "workspace.yaml").write_text("version: 1\nlayout:\n  cache_dir: []\n", encoding="utf-8")

    result = runner.invoke(config_app, ["get", "layout.cache_dir", "--workspace", str(root)])

    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert result.stdout == ""
    assert "layout.cache_dir: expects a string" in result.stderr


def test_list_refuses_a_hand_edited_explicit_null(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "workspace.yaml").write_text("version: 1\nlayout:\n  bundle_dir: null\n", encoding="utf-8")

    result = runner.invoke(config_app, ["list", "--workspace", str(root)])

    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert result.stdout == ""
    assert "layout.bundle_dir: is explicitly null" in result.stderr


def test_list_maps_a_broken_catalog_to_the_generic_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`list` resolves the whole catalog, so a registry fault there is a tool bug, not a schema one."""
    root = _workspace(tmp_path)

    def raise_registry_error(*_args: object, **_kwargs: object) -> object:
        raise RegistryError("catalog entry 'roles.*' has no resolver")

    monkeypatch.setattr(config_main.manifest, "resolve_checked_all", raise_registry_error)

    result = runner.invoke(config_app, ["list", "--workspace", str(root)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "Error: catalog entry 'roles.*' has no resolver" in result.stderr


# --- the local origin in the read views -------------------------------------


def test_get_names_the_local_origin_and_the_shadowed_committed_value(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "workspace.yaml").write_text("version: 1\ntopic: Committed\n", encoding="utf-8")
    (root / "workspace.local.yaml").write_text("topic: Laptop\n", encoding="utf-8")

    result = runner.invoke(config_app, ["get", "topic", "--workspace", str(root)])

    assert result.exit_code == 0
    assert "(origin: local)" in result.stdout
    assert "workspace.yaml value 'Committed' is shadowed by workspace.local.yaml" in result.stdout


def test_get_json_carries_the_local_origin(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "workspace.yaml").write_text("version: 1\ntopic: Committed\n", encoding="utf-8")
    (root / "workspace.local.yaml").write_text("topic: Laptop\n", encoding="utf-8")

    result = runner.invoke(config_app, ["get", "topic", "--workspace", str(root), "--json"])

    payload = json.loads(result.stdout)
    assert (payload["origin"], payload["value"], payload["shadowed"]) == ("local", "Laptop", "Committed")


def test_list_marks_a_locally_overridden_row(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "workspace.local.yaml").write_text("topic: Laptop\n", encoding="utf-8")

    result = runner.invoke(config_app, ["list", "--workspace", str(root)])

    assert result.exit_code == 0
    assert "~ topic = 'Laptop'" in result.stdout


def test_the_marker_table_covers_every_origin_a_resolved_can_carry() -> None:
    # A fourth origin with no marker is a KeyError at render time, not a
    # missing glyph — so the table and the vocabulary move together.
    entry = ConfigEntry(key="topic", type="str", default=None, description="Display name.")
    for origin in ("env", "local", "manifest", "default"):
        rendered = render_resolved_list([Resolved("topic", "x", origin, entry)], json_output=False)
        assert "topic = 'x'" in rendered

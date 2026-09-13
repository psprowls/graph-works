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
    (root / "workspace.yaml").write_text("version: 1\nworkflow: {dispatch_rules: dispatch.yaml}\n", encoding="utf-8")
    (root / "dispatch.yaml").write_text("pipeline: {rules: []}\n", encoding="utf-8")
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
    assert stored["workflow"] == {"dispatch_rules": "dispatch.yaml", "auto_drive": {"max_parallel": 3}}
    projection = json.loads((root / ".gw" / "cache" / "config.json").read_text(encoding="utf-8"))
    assert projection["workflow"]["auto_drive"]["max_parallel"] == 3
    assert set(projection["_meta"]) == {
        "source_mtime",
        "source_sha256",
        "overlay_mtime",
        "overlay_sha256",
        "dispatch_inputs",
    }


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
    assert stored["workflow"] == {"dispatch_rules": "dispatch.yaml"}
    projection = json.loads((root / ".gw" / "cache" / "config.json").read_text(encoding="utf-8"))
    assert projection["workflow"] == {"dispatch_rules": "dispatch.yaml"}


def test_set_rejects_invalid_values_without_writing(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(
        config_app,
        ["set", "workflow.auto_drive.max_parallel", "many", "--workspace", str(root)],
    )

    assert result.exit_code == exit_codes.GENERIC
    assert "expects an integer" in result.stderr
    assert PlainYamlStore(root / "workspace.yaml").read_explicit() == {
        "version": 1,
        "workflow": {"dispatch_rules": "dispatch.yaml"},
    }
    assert not (root / ".gw" / "cache" / "config.json").exists()


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
    PlainYamlStore(root / "workspace.yaml").write(
        {"version": 1, "topic": "Hand edited", "workflow": {"dispatch_rules": "dispatch.yaml"}}
    )

    result = runner.invoke(config_app, ["sync", "--workspace", str(root)])

    target = root / ".gw" / "cache" / "config.json"
    assert result.exit_code == 0
    assert result.stdout == f"[ok] projection: {target}\n"
    assert json.loads(target.read_text(encoding="utf-8"))["topic"] == "Hand edited"


def test_sync_json_names_the_projection_path(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(config_app, ["sync", "--workspace", str(root), "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"projection": str(root / ".gw" / "cache" / "config.json")}


def test_set_does_not_reorder_the_manifest(tmp_path: Path) -> None:
    # D-003: ruamel's safe representer sorts mapping keys by default, which
    # would turn a one-key write into a whole-file reordering diff. `version`
    # landing anywhere but first is the symptom.
    root = _workspace(tmp_path)
    manifest = root / "workspace.yaml"
    PlainYamlStore(manifest).write(
        {"version": 1, "topic": "t", "ignore": ["tmp/**"], "workflow": {"dispatch_rules": "dispatch.yaml"}}
    )
    before = [
        line.split(":", 1)[0]
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith((" ", "-", "#"))
    ]

    result = runner.invoke(config_app, ["set", "topic", "Reordered?", "--workspace", str(root)])

    assert result.exit_code == 0
    after = [
        line.split(":", 1)[0]
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith((" ", "-", "#"))
    ]
    assert after == before
    assert after[0] == "version"


def test_sync_maps_a_registry_fault_to_the_generic_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A projection that cannot resolve the registry is a tool fault, not a malformed store."""
    root = _workspace(tmp_path)

    def raise_registry_error(*_args: object, **_kwargs: object) -> object:
        raise RegistryError("no projection resolver for 'workflow.*'")

    monkeypatch.setattr(config_main, "write_dispatch_projection", raise_registry_error)

    result = runner.invoke(config_app, ["sync", "--workspace", str(root)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "Error: no projection resolver for 'workflow.*'" in result.stderr


# --- the --local layer ------------------------------------------------------


def test_set_local_writes_only_the_local_file_and_projects_the_merged_view(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    committed_before = (root / "workspace.yaml").read_bytes()

    result = runner.invoke(
        config_app,
        ["set", "--local", "workflow.auto_drive.max_parallel", "2", "--workspace", str(root), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["value"] == 2
    assert payload["origin"] == "local"
    assert (root / "workspace.yaml").read_bytes() == committed_before
    local = PlainYamlStore(root / "workspace.local.yaml").read_explicit()
    assert local == {"workflow": {"auto_drive": {"max_parallel": 2}}}
    projection = json.loads((root / ".gw" / "cache" / "config.json").read_text(encoding="utf-8"))
    assert projection["workflow"]["auto_drive"]["max_parallel"] == 2
    assert projection["_meta"]["overlay_sha256"] is not None


def test_set_local_shadows_a_committed_value_without_touching_it(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    runner.invoke(config_app, ["set", "workflow.auto_drive.max_parallel", "4", "--workspace", str(root)])

    result = runner.invoke(
        config_app,
        ["set", "--local", "workflow.auto_drive.max_parallel", "2", "--workspace", str(root), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert (payload["value"], payload["origin"], payload["shadowed"]) == (2, "local", 4)
    committed = PlainYamlStore(root / "workspace.yaml").read_explicit()
    assert committed["workflow"] == {"dispatch_rules": "dispatch.yaml", "auto_drive": {"max_parallel": 4}}


def test_set_without_local_still_writes_only_the_committed_file(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    runner.invoke(config_app, ["set", "topic", "Committed", "--workspace", str(root)])
    assert not (root / "workspace.local.yaml").exists()
    assert PlainYamlStore(root / "workspace.yaml").read_explicit()["topic"] == "Committed"


def test_a_base_set_reports_the_true_effective_value_when_a_local_override_shadows_it(tmp_path: Path) -> None:
    # I-1: writing the base while a workspace.local.yaml override already
    # exists must not claim the write took effect on this machine — the
    # rendered origin/value have to match what `get` would show immediately
    # afterward.
    root = _workspace(tmp_path)
    runner.invoke(config_app, ["set", "--local", "topic", "Laptop", "--workspace", str(root)])

    result = runner.invoke(
        config_app,
        ["set", "topic", "Renamed", "--workspace", str(root), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert (payload["value"], payload["origin"], payload["shadowed"]) == ("Laptop", "local", "Renamed")
    # The base write itself still happened — only the *reported* value changes.
    assert PlainYamlStore(root / "workspace.yaml").read_explicit()["topic"] == "Renamed"


def test_unset_local_removes_the_override_and_falls_back_to_the_base(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    runner.invoke(config_app, ["set", "workflow.auto_drive.max_parallel", "4", "--workspace", str(root)])
    runner.invoke(config_app, ["set", "--local", "workflow.auto_drive.max_parallel", "2", "--workspace", str(root)])

    result = runner.invoke(
        config_app,
        ["unset", "--local", "workflow.auto_drive.max_parallel", "--workspace", str(root), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert (payload["value"], payload["origin"]) == (4, "manifest")
    assert PlainYamlStore(root / "workspace.local.yaml").read_explicit() == {}
    projection = json.loads((root / ".gw" / "cache" / "config.json").read_text(encoding="utf-8"))
    assert projection["workflow"]["auto_drive"]["max_parallel"] == 4


def test_a_failed_first_local_write_leaves_no_local_file_behind(tmp_path: Path) -> None:
    # "nope" fails int() coercion inside `coerce()`, before `set_key` ever
    # reads or writes the overlay's store — so no rollback fires at all; there
    # is simply nothing on disk yet for a refused first --local write to
    # create.
    root = _workspace(tmp_path)
    result = runner.invoke(
        config_app,
        ["set", "--local", "workflow.auto_drive.max_parallel", "nope", "--workspace", str(root)],
    )
    assert result.exit_code != 0
    assert not (root / "workspace.local.yaml").exists()


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("document", [None, "pipeline: null\n", "pipeline: {rules: [{match: {}, agent: null}]}\n"])
def test_reference_write_validates_selected_pair_before_mutation(tmp_path, local, document):
    root = _workspace(tmp_path)
    target = root / ("workspace.local.yaml" if local else "workspace.yaml")
    before = target.read_bytes() if target.exists() else None
    if document is not None:
        (root / "other.yaml").write_text(document, encoding="utf-8", newline="")
    result = runner.invoke(
        config_app,
        ["set", "workflow.dispatch_rules", "other.yaml", "--workspace", str(root), *(["--local"] if local else [])],
    )
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert (target.read_bytes() if target.exists() else None) == before
    assert not (root / ".gw/cache/config.json").exists()


def test_reference_write_validates_new_local_sibling_and_projects_provenance(tmp_path):
    root = _workspace(tmp_path)
    shared = root / "other.yml"
    shared.write_text("# shared\npipeline: {rules: []}\n", encoding="utf-8", newline="")
    sibling = root / "other.local.yml"
    sibling.write_text("pipeline: null\n", encoding="utf-8", newline="")
    args = ["set", "--local", "workflow.dispatch_rules", "other.yml", "--workspace", str(root)]
    result = runner.invoke(config_app, args)
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert not (root / "workspace.local.yaml").exists()
    sibling.write_text("pipeline: {rules: []}\n", encoding="utf-8", newline="")
    before = shared.read_bytes()
    result = runner.invoke(config_app, args)
    assert result.exit_code == 0, result.output
    for verb in ["get", "list"]:
        result = runner.invoke(
            config_app,
            [verb, *(["workflow.dispatch_rules"] if verb == "get" else []), "--workspace", str(root), "--json"],
        )
        value = json.loads(result.stdout)
        row = value if verb == "get" else next(row for row in value if row["key"] == "workflow.dispatch_rules")
        assert (row["origin"], row["value"], row["shadowed"]) == ("local", "other.yml", "dispatch.yaml")
    payload = json.loads((root / ".gw/cache/config.json").read_bytes())
    assert payload["dispatch"]["shared_path"] == str(shared)
    assert payload["dispatch"]["local_path"] == str(sibling)
    assert shared.read_bytes() == before


def test_sync_refuses_invalid_rules_and_leaves_prior_projection_untouched(tmp_path):
    root = _workspace(tmp_path)
    assert runner.invoke(config_app, ["sync", "--workspace", str(root)]).exit_code == 0
    target = root / ".gw/cache/config.json"
    before = target.read_bytes()
    (root / "dispatch.yaml").write_text("pipeline: {rules: [{match: {}, mode: null}]}\n", encoding="utf-8", newline="")
    result = runner.invoke(config_app, ["sync", "--workspace", str(root)])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert target.read_bytes() == before

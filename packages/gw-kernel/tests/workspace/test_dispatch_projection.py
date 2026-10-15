"""Projection inputs are complete, validated, and checked before publication."""

import json
from pathlib import Path

import pytest
from config_io.errors import UnknownKeyError
from gw_kernel.workspace.dispatch_projection import (
    _check_projection_inputs,
    build_dispatch_projection,
    mutate_workspace_config,
    write_dispatch_projection,
)
from gw_kernel.workspace.errors import WorkspaceError
from gw_kernel.workspace.layout import layout_for
from gw_kernel.workspace.manifest import resolve_checked_key
from ruamel.yaml import YAML


def workspace(tmp_path: Path):
    (tmp_path / "workspace.yaml").write_text(
        "version: 1\nworkflow_config: dispatch.yaml\n", encoding="utf-8", newline=""
    )
    (tmp_path / "dispatch.yaml").write_text(
        "pipeline:\n  rules:\n    - name: shared\n      match: {}\n      model: example\n", encoding="utf-8", newline=""
    )
    return layout_for(tmp_path)


def test_projection_tracks_all_inputs_and_rule_origins(tmp_path):
    layout = workspace(tmp_path)
    payload = build_dispatch_projection(layout)
    assert payload["dispatch"]["rules"][0]["origin"]["source"] == str(tmp_path / "dispatch.yaml")
    assert payload["dispatch"]["reference"] == "dispatch.yaml"
    inputs = payload["_meta"]["dispatch_inputs"]
    assert set(inputs) == {"manifest", "manifest_local", "shared", "local"}
    assert inputs["local"] == {"path": str(tmp_path / "dispatch.local.yaml"), "exists": False, "sha256": None}
    assert payload["_meta"]["source_sha256"] == inputs["manifest"]["sha256"]
    assert payload["_meta"]["overlay_sha256"] is None
    assert "profile" not in payload["dispatch"]


def test_projection_carries_the_full_path_fold_and_artifacts(tmp_path):
    layout = workspace(tmp_path)
    (tmp_path / "dispatch.local.yaml").write_text(
        "pipeline:\n  path:\n    - name: f\n      match: {type: [Feature, Bug]}\n"
        "      stages: [design, execute, finish]\n"
        "  artifacts:\n    plan: {file: plan.md}\n",
        encoding="utf-8",
        newline="",
    )
    payload = build_dispatch_projection(layout)
    path = payload["dispatch"]["path"]
    assert path[0] == {
        "name": "default",
        "match": {},
        "stages": ["design", "plan", "execute", "finish"],
        "origin": {"source": "packaged", "index": 0, "name": "default"},
    }
    assert path[-1] == {
        "name": "f",
        "match": {"type": ["Feature", "Bug"]},
        "stages": ["design", "execute", "finish"],
        "origin": {"source": str(tmp_path / "dispatch.local.yaml"), "index": 0, "name": "f"},
    }
    assert payload["dispatch"]["artifacts"]["plan"] == {
        "file": "plan.md",
        "source": "plan",
        "required": True,
        "origin": str(tmp_path / "dispatch.local.yaml"),
    }
    assert payload["dispatch"]["artifacts"]["execute"]["origin"] == "packaged"
    json.loads(json.dumps(payload, default=str))


@pytest.mark.parametrize(
    "edit",
    [
        "pipeline:\n  path:\n    - match: {}\n      stages: [execute, finish]\n",
        "pipeline:\n  artifacts:\n    design: {file: spec.md}\n",
    ],
)
def test_editing_either_new_block_stales_the_projection(tmp_path, edit):
    layout = workspace(tmp_path)
    payload = build_dispatch_projection(layout)
    (tmp_path / "dispatch.local.yaml").write_text(edit, encoding="utf-8", newline="")
    with pytest.raises(WorkspaceError, match="configuration changed"):
        _check_projection_inputs(payload)


def test_failed_sync_preserves_previous_projection(tmp_path):
    layout = workspace(tmp_path)
    target = write_dispatch_projection(layout)
    before = target.read_bytes()
    (tmp_path / "dispatch.local.yaml").write_text("pipeline: null\n", encoding="utf-8", newline="")
    with pytest.raises(WorkspaceError, match="pipeline"):
        write_dispatch_projection(layout)
    assert target.read_bytes() == before


def test_projection_refuses_changes_during_assembly(tmp_path, monkeypatch):
    from gw_kernel.workspace import dispatch_projection as module

    layout = workspace(tmp_path)
    real = module.load_dispatch_config

    def changing(layout):
        config = real(layout)
        (tmp_path / "dispatch.local.yaml").write_text("pipeline: {rules: []}\n", encoding="utf-8", newline="")
        return config

    monkeypatch.setattr(module, "load_dispatch_config", changing)
    with pytest.raises(WorkspaceError, match="changed"):
        write_dispatch_projection(layout)
    assert not (layout.cache_dir / "config.json").exists()


def test_manifest_write_refuses_a_change_during_validation_without_clobbering_it(tmp_path, monkeypatch):
    from gw_kernel.workspace import dispatch_projection as module

    layout = workspace(tmp_path)
    real = module.load_prospective_dispatch_config

    def changing(layout, **kwargs):
        config = real(layout, **kwargs)
        layout.manifest_path.write_text(
            "version: 1\n# concurrent author\nworkflow_config: dispatch.yaml\n", encoding="utf-8", newline=""
        )
        return config

    monkeypatch.setattr(module, "load_prospective_dispatch_config", changing)
    with pytest.raises(WorkspaceError, match="changed"):
        module.mutate_workspace_config(layout, "topic", "new", local=False)
    assert "# concurrent author" in layout.manifest_path.read_text()
    assert "topic" not in layout.manifest_path.read_text()


def test_manifest_write_restores_bytes_on_projection_failure(tmp_path, monkeypatch):
    from gw_kernel.workspace import dispatch_projection as module

    layout = workspace(tmp_path)
    before = layout.manifest_path.read_bytes()

    def refuse(layout):
        raise WorkspaceError("publication refused")

    monkeypatch.setattr(module, "write_dispatch_projection", refuse)
    with pytest.raises(WorkspaceError, match="publication refused"):
        module.mutate_workspace_config(layout, "topic", "new", local=False)
    assert layout.manifest_path.read_bytes() == before


def test_manifest_write_restores_bytes_on_partial_write_failure(tmp_path, monkeypatch):
    from config_io import PlainYamlStore
    from gw_kernel.workspace import dispatch_projection as module

    layout = workspace(tmp_path)
    before = layout.manifest_path.read_bytes()

    def fail_write(store, data):
        store.path.write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(PlainYamlStore, "write", fail_write)
    with pytest.raises(OSError, match="disk full"):
        module.mutate_workspace_config(layout, "topic", "new", local=False)
    assert layout.manifest_path.read_bytes() == before


@pytest.mark.parametrize("local", [False, True])
def test_failed_projection_preserves_edit_after_completed_manifest_write(tmp_path, monkeypatch, local):
    from gw_kernel.workspace import dispatch_projection as module

    layout = workspace(tmp_path)
    target = layout.local_manifest_path if local else layout.manifest_path
    concurrent = b"version: 1\n# concurrent author\ntopic: concurrent\nworkflow_config: dispatch.yaml\n"

    def change_then_refuse(layout):
        assert b"topic: new" in target.read_bytes()
        target.write_bytes(concurrent)
        raise WorkspaceError("publication refused")

    monkeypatch.setattr(module, "write_dispatch_projection", change_then_refuse)
    with pytest.raises(WorkspaceError, match=r"rollback refused.*concurrent"):
        module.mutate_workspace_config(layout, "topic", "new", local=local)
    assert target.read_bytes() == concurrent


@pytest.mark.parametrize("local", [False, True])
def test_config_io_rollback_preserves_edit_during_post_write_validation(tmp_path, monkeypatch, local):
    from config_io import PlainYamlStore, StoreValidationError
    from gw_kernel.workspace import dispatch_projection as module

    layout = workspace(tmp_path)
    target = layout.local_manifest_path if local else layout.manifest_path
    concurrent = b"# concurrent author\ntopic: [unfinished\n"
    read = PlainYamlStore.read

    def change_then_refuse(store):
        if store.path == target and store.path.exists() and b"topic: new" in store.path.read_bytes():
            store.path.write_bytes(concurrent)
            raise StoreValidationError("concurrent unfinished edit")
        return read(store)

    monkeypatch.setattr(PlainYamlStore, "read", change_then_refuse)
    with pytest.raises(WorkspaceError, match=r"rollback refused.*concurrent"):
        module.mutate_workspace_config(layout, "topic", "new", local=local)
    assert target.read_bytes() == concurrent


def _ws(tmp_path: Path):
    (tmp_path / "workspace.yaml").write_text(
        "version: 1\nworkflow_config: workflow.yaml\n", encoding="utf-8", newline=""
    )
    (tmp_path / "workflow.yaml").write_text("pipeline: {}\n", encoding="utf-8", newline="")
    return layout_for(tmp_path)


def _yaml(path: Path) -> dict[str, object]:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}


def test_set_writes_a_workflow_key_into_the_shared_workflow_file(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    manifest_before = layout.manifest_path.read_bytes()
    mutate_workspace_config(layout, "workflow.auto_drive.max_parallel", "3", local=False)
    assert _yaml(tmp_path / "workflow.yaml") == {"pipeline": {}, "workflow": {"auto_drive": {"max_parallel": 3}}}
    assert layout.manifest_path.read_bytes() == manifest_before
    projection = json.loads((layout.cache_dir / "config.json").read_text(encoding="utf-8"))
    assert projection["workflow"] == {"auto_drive": {"max_parallel": 3}}
    assert projection["dispatch"]["reference"] == "workflow.yaml"


def test_set_local_creates_the_local_sibling_and_leaves_shared_bytes_alone(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    shared_before = (tmp_path / "workflow.yaml").read_bytes()
    mutate_workspace_config(layout, "workflow.gate.max_concurrent", "2", local=True)
    assert _yaml(tmp_path / "workflow.local.yaml") == {"workflow": {"gate": {"max_concurrent": 2}}}
    assert (tmp_path / "workflow.yaml").read_bytes() == shared_before
    assert resolve_checked_key(layout, "workflow.gate.max_concurrent", environ={}).origin == "workflow-local"


def test_unset_local_removes_the_key(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    mutate_workspace_config(layout, "workflow.gate.max_concurrent", "2", local=True)
    mutate_workspace_config(layout, "workflow.gate.max_concurrent", None, local=True)
    assert "workflow" not in _yaml(tmp_path / "workflow.local.yaml")
    assert resolve_checked_key(layout, "workflow.gate.max_concurrent", environ={}).origin == "default"


def test_a_workflow_write_over_a_broken_rule_is_refused_and_changes_nothing(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    (tmp_path / "workflow.yaml").write_text("pipeline:\n  rules: 5\n", encoding="utf-8", newline="")
    before = (tmp_path / "workflow.yaml").read_bytes()
    with pytest.raises(WorkspaceError):
        mutate_workspace_config(layout, "workflow.auto_drive.max_parallel", "3", local=False)
    assert (tmp_path / "workflow.yaml").read_bytes() == before


def test_an_unknown_workflow_key_without_a_pointer_is_refused_as_unknown(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text("version: 1\n", encoding="utf-8", newline="")
    layout = layout_for(tmp_path)
    before = layout.manifest_path.read_bytes()
    with pytest.raises(UnknownKeyError) as caught:
        mutate_workspace_config(layout, "workflow.typo", "1", local=False)
    assert "gw bootstrap" not in str(caught.value)
    assert "workflow.typo" in str(caught.value)
    assert layout.manifest_path.read_bytes() == before


def test_setting_workflow_config_to_a_missing_file_is_refused(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    before = layout.manifest_path.read_bytes()
    with pytest.raises(WorkspaceError, match=r"does not exist; run `gw bootstrap`"):
        mutate_workspace_config(layout, "workflow_config", "nope.yaml", local=False)
    assert layout.manifest_path.read_bytes() == before


def test_a_workflow_block_edit_invalidates_the_projection(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    payload = build_dispatch_projection(layout)
    (tmp_path / "workflow.yaml").write_text(
        "pipeline: {}\nworkflow:\n  workspace_commits: 'on'\n", encoding="utf-8", newline=""
    )
    with pytest.raises(WorkspaceError, match="configuration changed before projection publication"):
        _check_projection_inputs(payload)

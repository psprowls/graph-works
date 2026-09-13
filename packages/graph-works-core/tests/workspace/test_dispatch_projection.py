"""Projection inputs are complete, validated, and checked before publication."""

from pathlib import Path

import pytest
from graph_works_core.workspace.dispatch_projection import build_dispatch_projection, write_dispatch_projection
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import layout_for


def workspace(tmp_path: Path):
    (tmp_path / "workspace.yaml").write_text(
        "version: 1\nworkflow: {dispatch_rules: dispatch.yaml}\n", encoding="utf-8", newline=""
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


def test_failed_sync_preserves_previous_projection(tmp_path):
    layout = workspace(tmp_path)
    target = write_dispatch_projection(layout)
    before = target.read_bytes()
    (tmp_path / "dispatch.local.yaml").write_text("pipeline: null\n", encoding="utf-8", newline="")
    with pytest.raises(WorkspaceError, match="pipeline"):
        write_dispatch_projection(layout)
    assert target.read_bytes() == before


def test_projection_refuses_changes_during_assembly(tmp_path, monkeypatch):
    from graph_works_core.workspace import dispatch_projection as module

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
    from graph_works_core.workspace import dispatch_projection as module

    layout = workspace(tmp_path)
    real = module.load_prospective_dispatch_config

    def changing(layout, **kwargs):
        config = real(layout, **kwargs)
        layout.manifest_path.write_text(
            "version: 1\n# concurrent author\nworkflow: {dispatch_rules: dispatch.yaml}\n", encoding="utf-8", newline=""
        )
        return config

    monkeypatch.setattr(module, "load_prospective_dispatch_config", changing)
    with pytest.raises(WorkspaceError, match="changed"):
        module.mutate_workspace_config(layout, "topic", "new", local=False)
    assert "# concurrent author" in layout.manifest_path.read_text()
    assert "topic" not in layout.manifest_path.read_text()


def test_manifest_write_restores_bytes_on_projection_failure(tmp_path, monkeypatch):
    from graph_works_core.workspace import dispatch_projection as module

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
    from graph_works_core.workspace import dispatch_projection as module

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
    from graph_works_core.workspace import dispatch_projection as module

    layout = workspace(tmp_path)
    target = layout.local_manifest_path if local else layout.manifest_path
    concurrent = b"version: 1\n# concurrent author\ntopic: concurrent\nworkflow: {dispatch_rules: dispatch.yaml}\n"

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
    from graph_works_core.workspace import dispatch_projection as module

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

from __future__ import annotations

import os
from pathlib import Path

import pytest
from graph_works_core.workspace.dispatch import resolve_dispatch
from graph_works_core.workspace.dispatch_config import (
    apply_dispatch_write,
    load_dispatch_config,
    plan_dispatch_write,
)
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import layout_for


def _workspace(tmp_path: Path, reference: str = "dispatch.yaml"):
    (tmp_path / "workspace.yaml").write_text(
        f"version: 1\nworkflow:\n  dispatch_rules: {reference}\n",
        encoding="utf-8",
        newline="",
    )
    return layout_for(tmp_path)


def test_local_rules_append_without_rewriting_shared(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    shared = tmp_path / "dispatch.yaml"
    shared.write_text(
        "pipeline:\n  rules:\n    - match: {}\n      model: opus\n",
        encoding="utf-8",
        newline="",
    )
    (tmp_path / "dispatch.local.yaml").write_text(
        "pipeline:\n  rules:\n    - match: {stage: execute}\n      agent: codex\n",
        encoding="utf-8",
        newline="",
    )
    before = shared.read_bytes()

    config = load_dispatch_config(layout)
    result = resolve_dispatch({"stage": "execute", "variant": "planned"}, rules=config.rules)

    assert result.profile.agent == "codex"
    assert result.profile.model is None
    assert result.provenance["agent"].rule.source == str(config.local_path)
    assert shared.read_bytes() == before


def test_duplicate_labels_are_not_deduplicated(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    (tmp_path / "dispatch.yaml").write_text(
        "pipeline:\n  rules:\n    - name: same\n      match: {}\n      model: first\n",
        encoding="utf-8",
        newline="",
    )
    (tmp_path / "dispatch.local.yaml").write_text(
        "pipeline:\n  rules:\n    - name: same\n      match: {}\n      model: second\n",
        encoding="utf-8",
        newline="",
    )

    config = load_dispatch_config(layout)

    assert [rule.origin.name for rule in config.rules] == ["same", "same"]
    assert resolve_dispatch({"variant": "planned"}, rules=config.rules).profile.model == "second"


@pytest.mark.parametrize("local", [None, "{}\n", "pipeline:\n  rules: []\n"])
def test_absent_or_empty_local_rules_contribute_none(tmp_path: Path, local: str | None) -> None:
    layout = _workspace(tmp_path)
    (tmp_path / "dispatch.yaml").write_text("pipeline: {}\n", encoding="utf-8", newline="")
    if local is not None:
        (tmp_path / "dispatch.local.yaml").write_text(local, encoding="utf-8", newline="")

    config = load_dispatch_config(layout)

    assert config.rules == ()


def test_attributes_default_and_local_declaration_override(tmp_path: Path) -> None:
    layout = _workspace(tmp_path, "config/custom.yml")
    shared = tmp_path / "config/custom.yml"
    shared.parent.mkdir()
    shared.write_text(
        "pipeline:\n  attributes: [stage, variant]\n  rules:\n    - match: {stage: execute}\n      model: shared\n",
        encoding="utf-8",
        newline="",
    )
    local = tmp_path / "config/custom.local.yml"
    local.write_text(
        "pipeline:\n"
        "  attributes: [stage, variant, effort]\n"
        "  rules:\n"
        "    - match: {effort: large}\n"
        "      model: local\n",
        encoding="utf-8",
        newline="",
    )

    config = load_dispatch_config(layout)

    assert config.shared_path == shared
    assert config.local_path == local
    assert config.attributes == frozenset({"stage", "variant", "effort"})
    assert len(config.rules) == 2


def test_local_manifest_can_override_reference_with_an_absolute_yml_path(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    external = tmp_path.parent / f"{tmp_path.name}-rules.yml"
    external.write_text("{}\n", encoding="utf-8", newline="")
    layout.local_manifest_path.write_text(f"workflow:\n  dispatch_rules: {external}\n", encoding="utf-8", newline="")

    config = load_dispatch_config(layout)

    assert config.shared_path == external
    assert config.local_path == external.with_name(f"{external.stem}.local.yml")


@pytest.mark.parametrize(
    ("layer", "text", "needle"),
    [
        ("shared", "[]\n", "mapping at the top level"),
        ("local", "[]\n", "mapping at the top level"),
        ("shared", "pipeline: {attributes: [unknown]}\n", "unknown declared attributes"),
        ("shared", "pipeline:\n  rules: nope\n", "pipeline.rules must be a list"),
        ("local", "pipeline:\n  rules: null\n", "pipeline.rules must be a list"),
    ],
)
def test_invalid_layer_is_named(tmp_path: Path, layer: str, text: str, needle: str) -> None:
    layout = _workspace(tmp_path)
    (tmp_path / "dispatch.yaml").write_text("{}\n", encoding="utf-8", newline="")
    path = tmp_path / ("dispatch.yaml" if layer == "shared" else "dispatch.local.yaml")
    path.write_text(text, encoding="utf-8", newline="")

    with pytest.raises(WorkspaceError) as excinfo:
        load_dispatch_config(layout)

    assert str(path) in str(excinfo.value)
    assert needle in str(excinfo.value)


@pytest.mark.parametrize("layer", ["shared", "local"])
@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("pipeline: null\n", "pipeline must be a mapping"),
        ("pipeline:\n  attributes: null\n", "attributes must be a list of strings"),
    ],
)
def test_present_null_pipeline_members_are_rejected(tmp_path: Path, layer: str, text: str, needle: str) -> None:
    layout = _workspace(tmp_path)
    shared = tmp_path / "dispatch.yaml"
    shared.write_text("{}\n", encoding="utf-8", newline="")
    path = shared if layer == "shared" else tmp_path / "dispatch.local.yaml"
    path.write_text(text, encoding="utf-8", newline="")

    with pytest.raises(WorkspaceError, match=needle) as excinfo:
        load_dispatch_config(layout)

    assert str(path) in str(excinfo.value)


def test_unreadable_optional_local_file_is_not_treated_as_absent(tmp_path: Path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    (tmp_path / "dispatch.yaml").write_text("{}\n", encoding="utf-8", newline="")
    local = tmp_path / "dispatch.local.yaml"
    local.write_text("{}\n", encoding="utf-8", newline="")
    original = Path.read_bytes

    def refuse(path: Path) -> bytes:
        if path == local:
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    with pytest.raises(WorkspaceError, match="denied"):
        load_dispatch_config(layout)


@pytest.mark.parametrize(
    "legacy",
    [
        "pipeline: null",
        "auto_drive:\n    models: null",
        "auto_drive:\n    overrides: []",
        "auto_drive:\n    permission_mode: null",
    ],
)
def test_retired_manifest_keys_are_rejected_by_layer_membership(tmp_path: Path, legacy: str) -> None:
    layout = _workspace(tmp_path)
    (tmp_path / "dispatch.yaml").write_text("{}\n", encoding="utf-8", newline="")
    (tmp_path / "workspace.local.yaml").write_text(
        f"workflow:\n  {legacy}\n",
        encoding="utf-8",
        newline="",
    )

    with pytest.raises(WorkspaceError, match="retired") as excinfo:
        load_dispatch_config(layout)

    assert str(layout.local_manifest_path) in str(excinfo.value)
    assert "remove" in str(excinfo.value).lower()


def test_unrelated_manifest_configuration_survives_dispatch_loading(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    layout.manifest_path.write_text(
        "version: 1\nroles:\n  planner:\n    model_id: x\nworkflow:\n  dispatch_rules: dispatch.yaml\n"
        "  auto_drive:\n    max_parallel: 7\n    supervise_merges: true\n",
        encoding="utf-8",
        newline="",
    )
    (tmp_path / "dispatch.yaml").write_text("{}\n", encoding="utf-8", newline="")

    load_dispatch_config(layout)

    assert "max_parallel: 7" in layout.manifest_path.read_text(encoding="utf-8")
    assert "model_id: x" in layout.manifest_path.read_text(encoding="utf-8")


def test_plan_validates_combination_and_apply_refuses_stale_other_layer(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    shared = tmp_path / "dispatch.yaml"
    local = tmp_path / "dispatch.local.yaml"
    shared.write_text("pipeline: {}\n", encoding="utf-8", newline="")
    local.write_text("{}\n", encoding="utf-8", newline="")
    before_shared = shared.read_bytes()

    plan = plan_dispatch_write(
        layout,
        layer="local",
        document={"pipeline": {"rules": [{"match": {}, "model": "new"}]}},
    )
    local_before = local.read_bytes()
    stat = shared.stat()
    shared.write_text("pipeline: {changed: true}\n", encoding="utf-8", newline="")
    os.utime(shared, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    with pytest.raises(WorkspaceError, match="changed since planning"):
        apply_dispatch_write(plan)

    assert local.read_bytes() == local_before
    assert shared.read_bytes() != before_shared


def test_plan_rejects_invalid_prospective_combination_without_writing(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    shared = tmp_path / "dispatch.yaml"
    shared.write_text("pipeline: {attributes: [variant]}\n", encoding="utf-8", newline="")
    before = shared.read_bytes()

    with pytest.raises(WorkspaceError, match="unknown or undeclared"):
        plan_dispatch_write(
            layout,
            layer="local",
            document={"pipeline": {"rules": [{"match": {"stage": "execute"}, "model": "x"}]}},
        )

    assert shared.read_bytes() == before
    assert not (tmp_path / "dispatch.local.yaml").exists()


def test_apply_writes_only_selected_layer(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    shared = tmp_path / "dispatch.yaml"
    shared.write_text("pipeline: {}\n", encoding="utf-8", newline="")
    before = shared.read_bytes()
    plan = plan_dispatch_write(
        layout,
        layer="local",
        document={"pipeline": {"rules": [{"match": {}, "model": "new"}]}},
    )

    apply_dispatch_write(plan)

    assert shared.read_bytes() == before
    assert load_dispatch_config(layout).rules[0].fields["model"] == "new"


def test_apply_can_remove_existing_local_rules_without_touching_shared(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    shared = tmp_path / "dispatch.yaml"
    local = tmp_path / "dispatch.local.yaml"
    shared.write_text(
        "pipeline:\n  rules:\n    - match: {}\n      model: shared\n",
        encoding="utf-8",
        newline="",
    )
    local.write_text(
        "pipeline:\n  rules:\n    - match: {}\n      model: local\n",
        encoding="utf-8",
        newline="",
    )
    before = shared.read_bytes()
    assert resolve_dispatch({"variant": "planned"}, rules=load_dispatch_config(layout).rules).profile.model == "local"

    plan = plan_dispatch_write(layout, layer="local", document={"pipeline": {"rules": []}})
    apply_dispatch_write(plan)

    assert shared.read_bytes() == before
    assert resolve_dispatch({"variant": "planned"}, rules=load_dispatch_config(layout).rules).profile.model == "shared"


@pytest.mark.parametrize("layer", ["shared", "local"])
def test_failed_dispatch_validation_preserves_edit_after_completed_write(tmp_path, monkeypatch, layer):
    from graph_works_core.workspace import dispatch_config as module

    layout = _workspace(tmp_path)
    (tmp_path / "dispatch.yaml").write_text("pipeline: {rules: []}\n", encoding="utf-8", newline="")
    document = {"pipeline": {"rules": [{"match": {}, "model": "new"}]}}
    plan = module.plan_dispatch_write(layout, layer=layer, document=document)
    concurrent = b"# concurrent author\npipeline: {rules: []}\n"
    load = module.load_dispatch_config

    def change_then_refuse(layout):
        if plan.path.exists() and b"model: new" in plan.path.read_bytes():
            plan.path.write_bytes(concurrent)
            raise WorkspaceError("post-write validation refused")
        return load(layout)

    monkeypatch.setattr(module, "load_dispatch_config", change_then_refuse)
    with pytest.raises(WorkspaceError, match=r"rollback refused.*concurrent"):
        module.apply_dispatch_write(plan)
    assert plan.path.read_bytes() == concurrent


def test_retired_removal_does_not_require_dispatch_config_and_preserves_other_layer(tmp_path):
    from graph_works_core.workspace.dispatch_config import remove_retired_config_key
    from graph_works_core.workspace.layout import layout_for
    from graph_works_core.workspace.manifest import workspace_store

    (tmp_path / "workspace.yaml").write_text(
        "version: 1\nworkflow:\n  pipeline: {single: {skill: old}}\n"
        "  auto_drive: {models: {plan: old}, overrides: [], permission_mode: old, max_parallel: 4}\n",
        encoding="utf-8",
    )
    (tmp_path / "workspace.local.yaml").write_text(
        "workflow:\n  pipeline: {single: {skill: local}}\n", encoding="utf-8"
    )
    layout = layout_for(tmp_path)
    value = remove_retired_config_key(layout, "workflow.pipeline.single.skill", local=False)
    assert value.value == "local"
    assert value.origin == "local"
    for key in ("workflow.auto_drive.models", "workflow.auto_drive.overrides", "workflow.auto_drive.permission_mode"):
        remove_retired_config_key(layout, key, local=False)
    remove_retired_config_key(layout, "workflow.pipeline", local=True)
    raw = workspace_store(layout).read_explicit()
    assert raw["workflow"]["auto_drive"] == {"max_parallel": 4}
    assert "pipeline" not in raw["workflow"]


@pytest.mark.parametrize("layer", ["shared", "local"])
@pytest.mark.parametrize("change", ["reference", "selected-layer"])
@pytest.mark.parametrize("timing", ["before-read", "after-read"])
def test_apply_refuses_inputs_changed_during_reload(tmp_path, monkeypatch, layer, change, timing):
    from graph_works_core.workspace import dispatch_config as module

    layout = _workspace(tmp_path)
    paths = [
        tmp_path / name for name in ("dispatch.yaml", "dispatch.local.yaml", "alternate.yaml", "alternate.local.yaml")
    ]
    for path in paths:
        path.write_bytes(b"pipeline: {rules: []}\n")
    plan = module.plan_dispatch_write(
        layout, layer=layer, document={"pipeline": {"rules": [{"match": {}, "model": "new"}]}}
    )
    load = module.load_dispatch_config
    changed_path = layout.manifest_path if change == "reference" else plan.path
    concurrent = (
        b"version: 1\nworkflow: {dispatch_rules: alternate.yaml}\n"
        if change == "reference"
        else b"# concurrent author\npipeline: {rules: []}\n"
    )
    before = {path: path.read_bytes() for path in paths}
    calls = 0

    def change_during_reload(layout):
        nonlocal calls
        calls += 1
        if calls == 1 and timing == "before-read":
            changed_path.write_bytes(concurrent)
        result = load(layout)
        if calls == 1 and timing == "after-read":
            changed_path.write_bytes(concurrent)
        return result

    monkeypatch.setattr(module, "load_dispatch_config", change_during_reload)
    with pytest.raises(WorkspaceError, match="configuration changed"):
        module.apply_dispatch_write(plan)

    assert changed_path.read_bytes() == concurrent
    assert {path: path.read_bytes() for path in paths if path != changed_path} == {
        path: content for path, content in before.items() if path != changed_path
    }

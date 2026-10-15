"""`workflow.*` reads come from the workflow file `workflow_config` names (design §Resolution and errors)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from gw_kernel.workspace.dispatch_config import load_dispatch_config
from gw_kernel.workspace.errors import WorkspaceError
from gw_kernel.workspace.layout import layout_for
from gw_kernel.workspace.manifest import (
    CATALOG,
    checked_int,
    read,
    resolve_checked_all,
    resolve_checked_key,
    set_value,
    value_source,
)

MOVED = (
    "workflow.auto_drive.max_parallel",
    "workflow.auto_drive.max_attend",
    "workflow.auto_drive.supervise_merges",
    "workflow.gate.unit_jobs",
    "workflow.gate.max_concurrent",
    "workflow.workspace_commits",
)


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def _ws(
    tmp_path: Path,
    *,
    manifest: str = "version: 1\nworkflow_config: workflow.yaml\n",
    manifest_local: str | None = None,
    shared: str | None = "pipeline: {}\n",
    local: str | None = None,
):
    _write(tmp_path / "workspace.yaml", manifest)
    if manifest_local is not None:
        _write(tmp_path / "workspace.local.yaml", manifest_local)
    if shared is not None:
        _write(tmp_path / "workflow.yaml", shared)
    if local is not None:
        _write(tmp_path / "workflow.local.yaml", local)
    return layout_for(tmp_path)


def _nested(key: str, value: str) -> str:
    parts = key.split(".")
    lines = [f"{'  ' * depth}{part}:" for depth, part in enumerate(parts[:-1])]
    lines.append(f"{'  ' * (len(parts) - 1)}{parts[-1]}: {value}")
    return "\n".join(lines) + "\n"


def test_the_catalog_replaces_the_nested_pointer_with_workflow_config() -> None:
    keys = [entry.key for entry in CATALOG]
    assert "workflow.dispatch_rules" not in keys
    entry = next(entry for entry in CATALOG if entry.key == "workflow_config")
    assert (entry.type, entry.default) == ("str", None)
    assert [key for key in keys if key.startswith("workflow.")] == list(MOVED)


def test_a_shared_value_reads_with_the_workflow_origin(tmp_path: Path) -> None:
    layout = _ws(tmp_path, shared="workflow:\n  auto_drive:\n    max_parallel: 5\n")
    resolved = resolve_checked_key(layout, "workflow.auto_drive.max_parallel", environ={})
    assert (resolved.value, resolved.origin) == (5, "workflow")
    assert value_source(layout, resolved) == tmp_path / "workflow.yaml"


def test_a_local_only_value_reads_with_the_workflow_local_origin(tmp_path: Path) -> None:
    layout = _ws(tmp_path, local="workflow:\n  auto_drive:\n    max_parallel: 7\n")
    resolved = resolve_checked_key(layout, "workflow.auto_drive.max_parallel", environ={})
    assert (resolved.value, resolved.origin, resolved.shadowed) == (7, "workflow-local", None)
    assert value_source(layout, resolved) == tmp_path / "workflow.local.yaml"


def test_local_wins_and_the_blocks_deep_merge(tmp_path: Path) -> None:
    layout = _ws(
        tmp_path,
        shared="workflow:\n  auto_drive:\n    max_parallel: 5\n    max_attend: 3\n",
        local="workflow:\n  auto_drive:\n    max_parallel: 7\n",
    )
    parallel = resolve_checked_key(layout, "workflow.auto_drive.max_parallel", environ={})
    assert (parallel.value, parallel.origin, parallel.shadowed) == (7, "workflow-local", 5)
    assert checked_int(layout, "workflow.auto_drive.max_attend") == 3


@pytest.mark.parametrize("key", MOVED)
def test_an_absent_key_in_a_present_file_takes_its_catalog_default(tmp_path: Path, key: str) -> None:
    resolved = resolve_checked_key(_ws(tmp_path), key, environ={})
    entry = next(entry for entry in CATALOG if entry.key == key)
    assert (resolved.value, resolved.origin) == (entry.default, "default")


@pytest.mark.parametrize("layer", ["shared", "local"])
def test_an_unknown_top_level_key_fails_naming_its_file(tmp_path: Path, layer: str) -> None:
    layout = _ws(tmp_path, **{layer: "rules: []\n"})
    name = "workflow.yaml" if layer == "shared" else "workflow.local.yaml"
    with pytest.raises(WorkspaceError, match=rf"{name}: unknown top-level key 'rules'"):
        resolve_checked_key(layout, "workflow.workspace_commits", environ={})


@pytest.mark.parametrize("layer", ["shared", "local"])
def test_a_misspelled_workflow_key_fails_naming_file_and_key(tmp_path: Path, layer: str) -> None:
    layout = _ws(tmp_path, **{layer: "workflow:\n  auto_drive:\n    max_attended: 2\n"})
    name = "workflow.yaml" if layer == "shared" else "workflow.local.yaml"
    with pytest.raises(WorkspaceError, match=rf"{name}: unknown key workflow\.auto_drive\.max_attended"):
        resolve_checked_key(layout, "workflow.auto_drive.max_attend", environ={})


def test_a_broken_rule_does_not_block_a_workflow_read_but_fails_dispatch(tmp_path: Path) -> None:
    layout = _ws(tmp_path, shared="workflow:\n  workspace_commits: 'off'\npipeline:\n  rules: 5\n")
    assert resolve_checked_key(layout, "workflow.workspace_commits", environ={}).value == "off"
    with pytest.raises(WorkspaceError):
        load_dispatch_config(layout)


def test_a_missing_pointer_fails_workflow_and_dispatch_reads(tmp_path: Path) -> None:
    layout = _ws(tmp_path, manifest="version: 1\n")
    for call in (
        lambda: resolve_checked_key(layout, "workflow.workspace_commits", environ={}),
        lambda: load_dispatch_config(layout),
    ):
        with pytest.raises(WorkspaceError, match=r"workflow_config is not set.*run `gw bootstrap`"):
            call()


def test_a_pointer_to_a_missing_file_fails_workflow_and_dispatch_reads(tmp_path: Path) -> None:
    layout = _ws(tmp_path, shared=None)
    for call in (
        lambda: resolve_checked_key(layout, "workflow.workspace_commits", environ={}),
        lambda: load_dispatch_config(layout),
    ):
        with pytest.raises(
            WorkspaceError, match=r"workflow\.yaml: the workflow file .* does not exist; run `gw bootstrap`"
        ):
            call()


def test_a_pointer_set_only_in_the_local_manifest_is_followed(tmp_path: Path) -> None:
    layout = _ws(tmp_path, manifest="version: 1\n", manifest_local="workflow_config: mine.yaml\n", shared=None)
    _write(tmp_path / "mine.yaml", "workflow:\n  gate:\n    max_concurrent: 2\npipeline: {}\n")
    assert checked_int(layout, "workflow.gate.max_concurrent") == 2
    assert load_dispatch_config(layout).shared_path == tmp_path / "mine.yaml"


@pytest.mark.parametrize(("layer", "name"), [("shared", "workflow.yaml"), ("local", "workflow.local.yaml")])
def test_a_wrong_type_names_the_file_it_came_from(tmp_path: Path, layer: str, name: str) -> None:
    layout = _ws(tmp_path, **{layer: 'workflow:\n  auto_drive:\n    max_parallel: "4"\n'})
    with pytest.raises(WorkspaceError, match=rf"{name}: workflow\.auto_drive\.max_parallel: expects an integer"):
        checked_int(layout, "workflow.auto_drive.max_parallel")


def test_an_explicit_null_for_a_defaulted_key_names_the_workflow_file(tmp_path: Path) -> None:
    layout = _ws(tmp_path, shared="workflow:\n  auto_drive:\n    max_parallel: null\n")
    with pytest.raises(WorkspaceError, match=r"workflow\.yaml: workflow\.auto_drive\.max_parallel: is explicitly null"):
        checked_int(layout, "workflow.auto_drive.max_parallel")


def test_the_legacy_pointer_fails_every_manifest_read(tmp_path: Path) -> None:
    layout = _ws(tmp_path, manifest="version: 1\nworkflow:\n  dispatch_rules: dispatch.yaml\n")
    for call in (
        lambda: read(layout.manifest_path),
        lambda: resolve_checked_key(layout, "topic", environ={}),
        lambda: resolve_checked_all(layout, environ={}),
        lambda: load_dispatch_config(layout),
    ):
        with pytest.raises(
            WorkspaceError, match=r"workflow\.dispatch_rules is replaced by the top-level key workflow_config"
        ):
            call()


@pytest.mark.parametrize("key", MOVED)
@pytest.mark.parametrize("layer", ["manifest", "manifest_local"])
def test_each_moved_key_in_either_manifest_layer_names_its_new_home(tmp_path: Path, key: str, layer: str) -> None:
    text = _nested(key, "1")
    if layer == "manifest":
        layout = _ws(tmp_path, manifest="version: 1\nworkflow_config: workflow.yaml\n" + text)
        expected = (
            rf"workspace\.yaml: workflow\.\* keys no longer live in workspace\.yaml: {re.escape(key)}; "
            r"move them under workflow: in the workflow file"
        )
    else:
        layout = _ws(tmp_path, manifest_local=text)
        expected = rf"workspace\.local\.yaml: .*{re.escape(key)}; move them under workflow: in the \.local sibling"
    with pytest.raises(WorkspaceError, match=expected):
        read(layout.manifest_path)


def test_a_retired_key_keeps_its_message(tmp_path: Path) -> None:
    layout = _ws(tmp_path, manifest="version: 1\nworkflow_config: workflow.yaml\nworkflow:\n  pipeline: {}\n")
    read(layout.manifest_path)
    with pytest.raises(WorkspaceError, match=r"retired key workflow\.pipeline; remove it"):
        load_dispatch_config(layout)


def test_resolve_checked_all_keeps_catalog_order_with_routed_origins(tmp_path: Path) -> None:
    layout = _ws(tmp_path, shared="workflow:\n  workspace_commits: 'on'\npipeline: {}\n")
    rows = resolve_checked_all(layout, environ={})
    concrete = [entry.key for entry in CATALOG if "*" not in entry.key]
    assert [row.key for row in rows][: len(concrete)] == concrete
    by_key = {row.key: row for row in rows}
    assert (by_key["workflow.workspace_commits"].value, by_key["workflow.workspace_commits"].origin) == (
        "on",
        "workflow",
    )
    assert (by_key["workflow_config"].value, by_key["workflow_config"].origin) == ("workflow.yaml", "manifest")


def test_set_value_refuses_a_workflow_key(tmp_path: Path) -> None:
    layout = _ws(tmp_path)
    before = layout.manifest_path.read_bytes()
    with pytest.raises(WorkspaceError, match=r"workflow\.gate\.unit_jobs: lives in the workflow file"):
        set_value(layout.manifest_path, "workflow.gate.unit_jobs", "3")
    assert layout.manifest_path.read_bytes() == before


def test_a_known_leaf_set_to_an_empty_mapping_is_refused_by_the_value_check(tmp_path: Path) -> None:
    layout = _ws(tmp_path, shared="workflow:\n  workspace_commits: {}\n")
    with pytest.raises(WorkspaceError, match=r"workflow\.yaml: workflow\.workspace_commits: expects a string"):
        resolve_checked_key(layout, "workflow.workspace_commits", environ={})


def test_invalid_yaml_in_the_manifest_or_the_workflow_file_is_a_workspace_error(tmp_path: Path) -> None:
    layout = _ws(tmp_path, shared="workflow: [unclosed\n")
    with pytest.raises(WorkspaceError, match=r"workflow\.yaml.*not valid YAML"):
        resolve_checked_key(layout, "workflow.workspace_commits", environ={})
    _write(layout.manifest_path, "version: [unclosed\n")
    with pytest.raises(WorkspaceError, match=r"workspace\.yaml.*not valid YAML"):
        resolve_checked_key(layout, "workflow.workspace_commits", environ={})


def test_value_source_names_the_manifest_layer_for_a_manifest_key(tmp_path: Path) -> None:
    layout = _ws(tmp_path, manifest="version: 1\nworkflow_config: workflow.yaml\ntopic: t\n")
    resolved = resolve_checked_key(layout, "topic", environ={})
    assert value_source(layout, resolved) == layout.manifest_path


def test_a_workspace_without_a_manifest_reads_every_workflow_key_as_its_default(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    assert not layout.manifest_path.exists()
    resolved = resolve_checked_key(layout, "workflow.workspace_commits", environ={})
    assert (resolved.value, resolved.origin) == ("auto", "default")
    assert checked_int(layout, "workflow.auto_drive.max_parallel") == 2
    by_key = {item.key: item for item in resolve_checked_all(layout, environ={})}
    assert {key: by_key[key].origin for key in MOVED} == dict.fromkeys(MOVED, "default")
    assert by_key["workflow.gate.unit_jobs"].value is None


def test_a_stray_workflow_file_without_a_manifest_is_never_read(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    _write(tmp_path / "workflow.yaml", "workflow:\n  workspace_commits: 'off'\n")
    _write(tmp_path / "workflow.local.yaml", "workflow:\n  workspace_commits: 'on'\n")
    resolved = resolve_checked_key(layout, "workflow.workspace_commits", environ={})
    assert (resolved.value, resolved.origin) == ("auto", "default")


def test_a_manifest_without_the_pointer_still_refuses_the_workflow_read(tmp_path: Path) -> None:
    layout = _ws(tmp_path, manifest="version: 1\n")
    with pytest.raises(WorkspaceError, match="gw bootstrap"):
        resolve_checked_key(layout, "workflow.workspace_commits", environ={})

"""The workflow file's pure rules: pointer, legacy keys, shape (design §Resolution and errors)."""

from __future__ import annotations

from pathlib import Path

import pytest
from gw_kernel.workspace.errors import WorkspaceError
from gw_kernel.workspace.workflow_file import (
    RETIRED_KEYS,
    check_legacy_workflow_keys,
    legacy_workflow_keys,
    missing_workflow_file,
    validate_workflow_document,
    workflow_file_path,
)

KEYS = frozenset(
    {
        "workflow.auto_drive.max_parallel",
        "workflow.auto_drive.max_attend",
        "workflow.auto_drive.supervise_merges",
        "workflow.gate.unit_jobs",
        "workflow.gate.max_concurrent",
        "workflow.workspace_commits",
    }
)
MANIFEST = Path("/ws/workspace.yaml")
LOCAL = Path("/ws/workspace.local.yaml")


def test_retired_keys_are_the_four_already_retired() -> None:
    assert RETIRED_KEYS == (
        "workflow.pipeline",
        "workflow.auto_drive.models",
        "workflow.auto_drive.overrides",
        "workflow.auto_drive.permission_mode",
    )


def test_legacy_keys_list_every_leaf_except_retired_ones() -> None:
    layer = {
        "workflow": {
            "dispatch_rules": "dispatch.yaml",
            "auto_drive": {"max_parallel": 4, "models": {"design": "opus"}},
            "pipeline": None,
        }
    }
    assert legacy_workflow_keys(layer) == ["workflow.auto_drive.max_parallel", "workflow.dispatch_rules"]


def test_an_empty_workflow_mapping_is_not_legacy() -> None:
    assert legacy_workflow_keys({"workflow": {}}) == []
    assert legacy_workflow_keys({"version": 1}) == []


def test_a_scalar_workflow_value_is_itself_legacy() -> None:
    assert legacy_workflow_keys({"workflow": None}) == ["workflow"]


def test_dispatch_rules_names_the_top_level_replacement() -> None:
    with pytest.raises(WorkspaceError) as raised:
        check_legacy_workflow_keys(
            manifest_path=MANIFEST,
            base={"workflow": {"dispatch_rules": "dispatch.yaml", "auto_drive": {"max_parallel": 4}}},
            local_manifest_path=LOCAL,
            overlay={},
        )
    message = str(raised.value)
    assert message.startswith(f"{MANIFEST}: workflow.dispatch_rules is replaced by the top-level key workflow_config")


@pytest.mark.parametrize("key", sorted(KEYS))
def test_a_moved_key_in_the_shared_manifest_names_the_workflow_file(key: str) -> None:
    head, *rest = key.split(".")[1:]
    block: dict[str, object] = {head: 1} if not rest else {head: {rest[0]: 1}}
    with pytest.raises(WorkspaceError) as raised:
        check_legacy_workflow_keys(
            manifest_path=MANIFEST, base={"workflow": block}, local_manifest_path=LOCAL, overlay={}
        )
    assert str(raised.value) == (
        f"{MANIFEST}: workflow.* keys no longer live in workspace.yaml: {key}; "
        "move them under workflow: in the workflow file named by workflow_config"
    )


def test_a_moved_key_in_the_local_manifest_names_the_local_sibling() -> None:
    with pytest.raises(WorkspaceError) as raised:
        check_legacy_workflow_keys(
            manifest_path=MANIFEST,
            base={},
            local_manifest_path=LOCAL,
            overlay={"workflow": {"auto_drive": {"max_attended": 2}}},
        )
    assert str(raised.value) == (
        f"{LOCAL}: workflow.* keys no longer live in workspace.local.yaml: workflow.auto_drive.max_attended; "
        "move them under workflow: in the .local sibling of the workflow file named by workflow_config"
    )


def test_retired_keys_pass_the_legacy_check_untouched() -> None:
    check_legacy_workflow_keys(
        manifest_path=MANIFEST, base={"workflow": {"pipeline": {}}}, local_manifest_path=LOCAL, overlay={}
    )


def test_the_pointer_resolves_against_the_manifest_directory_and_local_wins() -> None:
    assert workflow_file_path(manifest_path=MANIFEST, base={"workflow_config": "workflow.yaml"}, overlay={}) == Path(
        "/ws/workflow.yaml"
    )
    assert workflow_file_path(
        manifest_path=MANIFEST, base={"workflow_config": "workflow.yaml"}, overlay={"workflow_config": "mine.yaml"}
    ) == Path("/ws/mine.yaml")


def test_an_absolute_or_home_pointer_is_used_as_given(tmp_path: Path) -> None:
    absolute = tmp_path / "elsewhere.yaml"
    assert workflow_file_path(manifest_path=MANIFEST, base={"workflow_config": str(absolute)}, overlay={}) == absolute
    home = workflow_file_path(manifest_path=MANIFEST, base={"workflow_config": "~/wf.yaml"}, overlay={})
    assert home == Path("~/wf.yaml").expanduser()


def test_a_missing_pointer_says_run_bootstrap() -> None:
    with pytest.raises(WorkspaceError, match=r"workflow_config is not set.*run `gw bootstrap`"):
        workflow_file_path(manifest_path=MANIFEST, base={"version": 1}, overlay={})


@pytest.mark.parametrize("value", ["", "  ", 3, ["a"]])
def test_a_malformed_pointer_is_refused(value: object) -> None:
    with pytest.raises(WorkspaceError, match=r"workflow_config must be a non-empty path"):
        workflow_file_path(manifest_path=MANIFEST, base={"workflow_config": value}, overlay={})


def test_missing_workflow_file_says_run_bootstrap() -> None:
    error = missing_workflow_file(Path("/ws/workflow.yaml"))
    assert str(error) == "/ws/workflow.yaml: the workflow file workflow_config names does not exist; run `gw bootstrap`"


def test_a_well_formed_document_passes() -> None:
    validate_workflow_document(
        Path("w.yaml"),
        {"workflow": {"auto_drive": {"max_parallel": 3}, "workspace_commits": "on"}, "pipeline": {"rules": []}},
        keys=KEYS,
    )
    validate_workflow_document(Path("w.yaml"), {}, keys=KEYS)
    validate_workflow_document(Path("w.yaml"), {"workflow": {"gate": {}}}, keys=KEYS)


def test_an_unknown_top_level_key_is_refused_naming_file_and_key() -> None:
    with pytest.raises(WorkspaceError) as raised:
        validate_workflow_document(Path("w.local.yaml"), {"rules": []}, keys=KEYS)
    assert (
        str(raised.value)
        == "w.local.yaml: unknown top-level key 'rules'; a workflow file holds only workflow: and pipeline:"
    )


def test_an_unknown_workflow_key_is_refused_naming_file_and_key() -> None:
    with pytest.raises(
        WorkspaceError, match=r"^w\.yaml: unknown key workflow\.auto_drive\.max_attended; expected one of"
    ):
        validate_workflow_document(Path("w.yaml"), {"workflow": {"auto_drive": {"max_attended": 2}}}, keys=KEYS)


@pytest.mark.parametrize("block", [{"gate": 3}, {"auto_drive": None}])
def test_a_branch_that_is_not_a_mapping_is_refused(block: dict[str, object]) -> None:
    with pytest.raises(WorkspaceError, match=r"^w\.yaml: workflow\.(gate|auto_drive) must be a mapping$"):
        validate_workflow_document(Path("w.yaml"), {"workflow": block}, keys=KEYS)


@pytest.mark.parametrize("block", [None, 3, ["a"]])
def test_a_workflow_block_that_is_not_a_mapping_is_refused(block: object) -> None:
    with pytest.raises(WorkspaceError, match=r"^w\.yaml: workflow must be a mapping"):
        validate_workflow_document(Path("w.yaml"), {"workflow": block}, keys=KEYS)

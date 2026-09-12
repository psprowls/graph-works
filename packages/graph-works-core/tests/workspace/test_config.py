"""Workspace-owned loading of the declarations/configuration surface."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.errors import WorkspaceConfigError


def test_workspace_config_adapter_maps_schema_errors(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=date(2026, 8, 23), topic="Config")).layout

    assert load_workspace_config(layout).declarations_dir == layout.config_dir

    layout.manifest_path.write_text("version: [unclosed\n", encoding="utf-8")
    with pytest.raises(WorkspaceConfigError):
        load_workspace_config(layout)


def test_a_missing_manifest_raises_file_not_found_with_the_old_message(tmp_path: Path) -> None:
    # The existence check is load-bearing, not defensive: `load_config`
    # propagated OSError for a missing file and `PlainYamlStore._load`
    # returns {}. `workspace/repos.py` distinguishes the two outcomes, so the
    # OSError has to be raised here rather than emerge from the store.
    from graph_works_core.workspace.layout import layout_for

    layout = layout_for(tmp_path / "ws")
    with pytest.raises(FileNotFoundError) as excinfo:
        load_workspace_config(layout)

    try:
        layout.manifest_path.read_bytes()
    except FileNotFoundError as native:
        assert str(excinfo.value) == str(native)
    else:  # pragma: no cover -- the path does not exist, by construction
        pytest.fail("the manifest exists; this test asserts the absent case")


def test_a_non_mapping_top_level_raises_workspace_config_error(tmp_path: Path) -> None:
    # The parse moved into the store, so this arrives as StoreValidationError
    # and must still come out of the adapter as WorkspaceConfigError.
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=date(2026, 8, 23), topic="Config")).layout

    layout.manifest_path.write_text("- one\n- two\n", encoding="utf-8", newline="")
    with pytest.raises(WorkspaceConfigError, match="mapping"):
        load_workspace_config(layout)


def test_a_malformed_block_still_raises_workspace_config_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=date(2026, 8, 23), topic="Config")).layout

    layout.manifest_path.write_text("version: 1\nrepositories: [not, a, mapping]\n", encoding="utf-8", newline="")
    with pytest.raises(WorkspaceConfigError, match=r"workspace\.yaml"):
        load_workspace_config(layout)


def test_a_declared_repo_path_still_anchors_on_the_manifests_own_directory(tmp_path: Path) -> None:
    # ADR-0041, preserved across the move to `config_from_mapping(anchor=…)`.
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=date(2026, 8, 23), topic="Config")).layout

    layout.manifest_path.write_text(
        "version: 1\nrepositories:\n  gw:\n    path: ../code\n", encoding="utf-8", newline=""
    )
    config = load_workspace_config(layout)
    assert config.repos[0].path == (layout.manifest_path.parent / "../code").resolve()

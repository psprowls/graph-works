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

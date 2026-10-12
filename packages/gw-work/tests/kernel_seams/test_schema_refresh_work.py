"""`plan_schema_refresh` / `apply_schema_refresh`: provenance-guarded and all-or-nothing."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from gw_kernel import apply_init, plan_init
from gw_kernel.workspace import work_schemas as ws
from gw_kernel.workspace.layout import WorkspaceLayout

TODAY = date(2026, 10, 5)


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    layout = apply_init(plan_init(tmp_path / "workspace", today=TODAY, topic="Refresh")).layout
    _path(layout, ws.PROVENANCE_RELATIVE).unlink(missing_ok=True)
    return layout


def _path(layout: WorkspaceLayout, relative: str) -> Path:
    return ws.declarations_dir_for(layout) / relative


def test_missing_configured_schema_directory_is_repaired_in_place(layout: WorkspaceLayout) -> None:
    from gw_kernel.workspace.config import load_workspace_config
    from gw_work.commands import run_lint

    (layout.config_dir / "schema").rename(layout.root / "saved-schema")
    plan = ws.plan_schema_refresh(layout)
    assert plan.declarations_dir == layout.config_dir
    assert plan.ok and len(plan.writes) == 8
    ws.apply_schema_refresh(plan)
    assert ws.inspect_work_schemas(layout).drifted == ()
    assert not (layout.bundle_dir / "schema").exists()
    assert run_lint(layout, load_workspace_config(layout), today=TODAY).ok

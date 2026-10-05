"""Mechanical lint carries workspace schema drift as its own lane."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from code_wiki_okf.config import load_config
from graph_works_core import apply_init, plan_init
from graph_works_core.lint_drift.lint import run_mechanical
from graph_works_core.workspace import work_schemas as ws

TODAY = date(2026, 10, 5)


@pytest.mark.parametrize("missing", [False, True])
def test_workspace_lane_appears_only_on_drift(tmp_path: Path, missing: bool) -> None:
    layout = apply_init(plan_init(tmp_path / "w", today=TODAY, topic="L")).layout
    config = load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    assert all(lane.name != "workspace" for lane in run_mechanical(layout, config, today=TODAY).mechanical)
    target = ws.declarations_dir_for(layout) / "schema/Bug.schema.json"
    if missing:
        target.unlink()
    else:
        target.write_bytes(b"{}\n")
    report = run_mechanical(layout, config, today=TODAY)
    lanes = {lane.name: lane for lane in report.mechanical}
    assert [finding.code for finding in lanes["workspace"].report.findings] == [
        ws.SCHEMA_MISSING if missing else ws.SCHEMA_DRIFT
    ]
    assert not report.ok


@pytest.mark.parametrize(
    "broken", ["malformed", "missing-file", "missing-directory", "directory-target", "symlink-parent"]
)
def test_broken_owned_schema_retains_mechanical_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: str
) -> None:
    from graph_works_core.workspace.config import load_workspace_config

    layout = apply_init(plan_init(tmp_path / "w", today=TODAY, topic="L")).layout
    config = load_workspace_config(layout)
    target = layout.config_dir / "schema/_base.schema.json"
    if broken == "malformed":
        target.write_bytes(b"{broken")
    elif broken == "missing-file":
        target.unlink()
    elif broken == "missing-directory":
        target.parent.rename(layout.root / "saved-schema")
    elif broken == "directory-target":
        target.unlink()
        target.mkdir()
    else:
        outside = layout.root / "outside-schema"
        target.parent.rename(outside)
        try:
            target.parent.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation unavailable")
    if broken in {"directory-target", "symlink-parent"}:
        real_open = Path.open

        def no_unsafe_open(self: Path, *args: object, **kwargs: object) -> object:
            if self == target or (broken == "symlink-parent" and self.parent == target.parent):
                pytest.fail("unsafe owned declaration opened")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", no_unsafe_open)
    report = run_mechanical(layout, config, today=TODAY)
    assert not report.ok
    assert report.errors
    lanes = {lane.name: lane for lane in report.mechanical}
    assert any(
        f.path == ".gw/schema/_base.schema.json" and "gw config sync --schemas" in f.message
        for f in lanes["workspace"].report.findings
    )

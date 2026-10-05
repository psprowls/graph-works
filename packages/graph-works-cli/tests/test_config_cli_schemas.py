"""`gw config sync --schemas`: preview, apply, force; plain sync unchanged."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest
from config_io import PlainYamlStore
from graph_works_cli import exit_codes
from graph_works_cli.config_cli import main as config_main
from graph_works_cli.config_cli.main import config_app
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace import work_schemas as ws
from graph_works_core.workspace.layout import WorkspaceLayout
from typer.testing import CliRunner

runner = CliRunner()
BASE = "schema/_base.schema.json"


def _root(tmp_path: Path) -> Path:
    return apply_init(plan_init(tmp_path / "w", today=date(2026, 10, 5), topic="C")).layout.root


def _base(root: Path) -> Path:
    return root / ".gw" / BASE


def test_plain_sync_is_unchanged(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _base(root).write_bytes(b"{}\n")
    result = runner.invoke(config_app, ["sync", "--workspace", str(root)])
    assert result.exit_code == 0
    assert result.stdout == f"[ok] projection: {root / '.gw/cache/config.json'}\n"
    assert _base(root).read_bytes() == b"{}\n"


def test_preview_writes_nothing_and_shows_refusal_diff(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _base(root).write_bytes(b"{}\n")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = runner.invoke(config_app, ["sync", "--schemas", "--workspace", str(root)])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert f"! {BASE} refused (edited):" in result.stdout
    assert f"--- a/{BASE}" in result.stdout and f"+++ b/{BASE}" in result.stdout
    assert "preview only; re-run with --apply to write" in result.stdout
    assert "edited/unrecorded files need --force after review" in result.stdout
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_refused_apply_prints_json_preview_and_writes_nothing(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _base(root).write_bytes(b"{}\n")
    missing = root / ".gw/schema/Bug.schema.json"
    missing.unlink()
    result = runner.invoke(config_app, ["sync", "--schemas", "--apply", "--json", "--workspace", str(root)])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    payload = json.loads(result.stdout)
    assert payload["applied"] is False and payload["written"] == []
    assert payload["refusals"][0]["path"] == BASE
    assert payload["refusals"][0]["diff"].startswith(f"--- a/{BASE}")
    assert _base(root).read_bytes() == b"{}\n" and not missing.exists()


def test_force_preview_does_not_apply(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _base(root).write_bytes(b"{}\n")
    result = runner.invoke(config_app, ["sync", "--schemas", "--force", "--workspace", str(root)])
    assert result.exit_code == 0, result.output
    assert f"~ {BASE} (replace)" in result.stdout
    assert "preview only" in result.stdout
    assert _base(root).read_bytes() == b"{}\n"


def test_force_apply_replaces_and_is_then_clean(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _base(root).write_bytes(b"{}\n")
    result = runner.invoke(config_app, ["sync", "--schemas", "--force", "--apply", "--workspace", str(root)])
    assert result.exit_code == 0, result.output
    assert "[ok] wrote 1 file(s)" in result.stdout
    assert "commit: skipped" in result.stdout
    assert _base(root).read_bytes() == ws.packaged_schemas()[BASE]
    again = runner.invoke(config_app, ["sync", "--schemas", "--json", "--workspace", str(root)])
    payload = json.loads(again.stdout)
    assert again.exit_code == 0 and payload["writes"] == [] and payload["refusals"] == []


def test_missing_file_apply_without_force(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = root / ".gw/schema/Bug.schema.json"
    target.unlink()
    result = runner.invoke(config_app, ["sync", "--schemas", "--apply", "--json", "--workspace", str(root)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["applied"] is True
    assert payload["writes"][0]["created"] is True
    assert payload["written"] == [".gw/schema/Bug.schema.json"]
    assert target.read_bytes() == ws.packaged_schemas()["schema/Bug.schema.json"]


def test_preview_reports_current_files_and_provenance(tmp_path: Path) -> None:
    root = _root(tmp_path)
    provenance = root / ".gw" / ws.PROVENANCE_RELATIVE
    provenance.unlink()
    result = runner.invoke(config_app, ["sync", "--schemas", "--workspace", str(root)])
    assert result.exit_code == 0, result.output
    assert "= 8 current\n+ provenance\npreview only" in result.stdout
    assert not provenance.exists()


@pytest.mark.parametrize("flag", ["--apply", "--force"])
def test_schema_modifier_without_schemas_is_a_usage_error(tmp_path: Path, flag: str) -> None:
    root = _root(tmp_path)
    projection = root / ".gw/cache/config.json"
    before = projection.read_bytes()
    result = runner.invoke(config_app, ["sync", flag, "--workspace", str(root)])
    assert result.exit_code == 2
    assert "require --schemas" in result.stderr
    assert projection.read_bytes() == before


def test_apply_maps_stale_plan_to_stale_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    target = root / ".gw/schema/Bug.schema.json"
    target.unlink()

    def raced_plan(layout: WorkspaceLayout, *, force: bool = False) -> ws.SchemaRefreshPlan:
        plan = ws.plan_schema_refresh(layout, force=force)
        target.write_bytes(b"{}\n")
        return plan

    monkeypatch.setattr(config_main, "plan_schema_refresh", raced_plan, raising=False)
    result = runner.invoke(config_app, ["sync", "--schemas", "--apply", "--workspace", str(root)])
    assert result.exit_code == exit_codes.STALE
    assert "Error:" in result.stderr and result.stdout == ""
    assert target.read_bytes() == b"{}\n"


def test_apply_maps_workspace_error_to_schema_mismatch(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = root / ".gw/schema/Bug.schema.json"
    target.unlink()
    PlainYamlStore(root / "workspace.yaml").write(
        {"version": 1, "topic": "C", "workflow": {"dispatch_rules": "dispatch.yaml", "workspace_commits": "invalid"}}
    )
    result = runner.invoke(config_app, ["sync", "--schemas", "--apply", "--workspace", str(root)])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert "workspace_commits" in result.stderr and result.stdout == ""
    assert not target.exists()


def test_apply_maps_io_error_to_generic_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    target = root / ".gw/schema/Bug.schema.json"
    target.unlink()

    def failed_replace(path: Path, data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(ws, "_replace", failed_replace)
    result = runner.invoke(config_app, ["sync", "--schemas", "--apply", "--workspace", str(root)])
    assert result.exit_code == exit_codes.GENERIC
    assert "Error: disk full" in result.stderr and result.stdout == ""
    assert not target.exists()


def test_apply_reports_failed_rollback_and_changed_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    first = root / ".gw/schema/Feature.schema.json"
    second = root / ".gw/schema/Bug.schema.json"
    first.write_bytes(b"{}\n")
    second.write_bytes(b"{}\n")
    real_replace = os.replace

    def fail(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        target = Path(dst)
        if target == second:
            raise OSError("schema write failed")
        if target == first and first.read_bytes() != b"{}\n":
            raise OSError("schema restore failed")
        real_replace(src, dst)

    monkeypatch.setattr(ws.os, "replace", fail)
    result = runner.invoke(config_app, ["sync", "--schemas", "--force", "--apply", "--workspace", str(root)])
    assert result.exit_code == exit_codes.GENERIC
    assert first.read_bytes() == ws.packaged_schemas()["schema/Feature.schema.json"]
    assert second.read_bytes() == b"{}\n"
    assert "Error: schema write failed" in result.stderr
    assert f"schema refresh rollback failed for {first}: schema restore failed" in result.stderr


def test_unsafe_refusal_recommends_repair_without_force_retry(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = _base(root)
    target.unlink()
    target.mkdir()
    result = runner.invoke(config_app, ["sync", "--schemas", "--workspace", str(root)])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert "repair unsafe targets" in result.stdout
    assert "need --force" not in result.stdout


def test_mixed_refusals_distinguish_force_and_unsafe_repair(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _base(root).write_bytes(b"{}\n")
    unsafe = root / ".gw/schema/Bug.schema.json"
    unsafe.unlink()
    unsafe.mkdir()
    result = runner.invoke(config_app, ["sync", "--schemas", "--workspace", str(root)])
    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert "edited/unrecorded files need --force after review" in result.stdout
    assert "repair unsafe targets" in result.stdout

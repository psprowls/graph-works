"""Read-index diagnostics preserve core reports and maintenance exit semantics."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_core.util.read_index import ReadIndexBusy, ReadIndexUnavailable
from helpers import initialized_workspace as _initialized_workspace
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    return _initialized_workspace(tmp_path / "works")


def invoke(workspace: Path, *flags: str):
    return runner.invoke(app, ["util", "read-index", "--workspace", str(workspace), *flags])


def test_bare_inspect_exits_zero_and_creates_nothing(initialized_workspace: Path) -> None:
    before = set(initialized_workspace.rglob("*"))
    result = invoke(initialized_workspace, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert (payload["mode"], payload["exists"]) == ("inspect", False)
    assert not Path(payload["db_path"]).exists()
    assert set(initialized_workspace.rglob("*")) == before


def test_missing_database_human_report(initialized_workspace: Path) -> None:
    result = invoke(initialized_workspace)
    assert result.exit_code == 0, result.output
    assert " (missing)" in result.stdout
    assert "enabled: true" in result.stdout
    assert "backend: index\n" in result.stdout
    assert "fingerprint: stale" in result.stdout
    assert "drift: none" in result.stdout


def test_rebuild_then_inspect(initialized_workspace: Path) -> None:
    result = invoke(initialized_workspace, "--rebuild")
    assert result.exit_code == 0, result.output
    assert "rebuild:" in result.stdout and "files parsed" in result.stdout
    result = invoke(initialized_workspace, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["exists"] and payload["generation"] is not None
    assert payload["fingerprint"]["current"] == payload["fingerprint"]["stored"]
    assert payload["tables"]["members"] > 0


@pytest.mark.parametrize("timestamp_ns", [10**30, 253_402_300_800_000_000_000])
def test_out_of_range_real_cache_timestamp_is_reported_without_writes(
    initialized_workspace: Path, timestamp_ns: int
) -> None:
    result = invoke(initialized_workspace, "--rebuild", "--json")
    assert result.exit_code == 0, result.output
    database = Path(json.loads(result.stdout)["db_path"])
    connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE meta SET value = ? WHERE key = 'last_reconcile_ns'", (str(timestamp_ns),))
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
    finally:
        connection.close()

    database_bytes = database.read_bytes()
    file_set = set(initialized_workspace.rglob("*"))
    human = invoke(initialized_workspace)
    assert human.exit_code == 0, human.output
    assert f"last reconcile: {timestamp_ns} ns (out of datetime range)" in human.stdout
    assert "generation: 1" in human.stdout
    assert "table members:" in human.stdout

    json_result = invoke(initialized_workspace, "--json")
    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.stdout)["last_reconcile_ns"] == timestamp_ns
    assert database.read_bytes() == database_bytes
    assert set(initialized_workspace.rglob("*")) == file_set


@pytest.mark.parametrize("json_mode", [False, True])
def test_verify_drift_exits_stale(
    initialized_workspace: Path, monkeypatch: pytest.MonkeyPatch, json_mode: bool
) -> None:
    from graph_works_cli.util_cli import read_index as module

    real = module.run_read_index
    monkeypatch.setattr(
        module, "run_read_index", lambda layout, **kw: dataclasses.replace(real(layout, **kw), drift=("a.md",))
    )
    result = invoke(initialized_workspace, "--verify", *(["--json"] if json_mode else []))
    assert result.exit_code == exit_codes.STALE, result.output
    if json_mode:
        assert json.loads(result.stdout)["drift"] == ["a.md"]
    else:
        assert "drift:\n  a.md" in result.stdout


def test_verify_without_drift_exits_zero(initialized_workspace: Path) -> None:
    result = invoke(initialized_workspace, "--verify", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["drift"] == []


@pytest.mark.parametrize("flag", ["--verify", "--rebuild"])
@pytest.mark.parametrize("json_mode", [False, True])
def test_unsettled_new_members_exit_nonzero_without_clean_report(
    initialized_workspace: Path, monkeypatch: pytest.MonkeyPatch, flag: str, json_mode: bool
) -> None:
    from okf_ext import readindex
    from okf_ext.readindex import sync

    if flag == "--verify":
        warm = invoke(initialized_workspace, "--rebuild", "--json")
        assert warm.exit_code == 0, warm.output
    else:
        assert not list((initialized_workspace / ".gw" / "cache").rglob("*.sqlite*"))
    new_ids = ("new.md", "other-new.md")
    for mid in new_ids:
        (initialized_workspace / "okf" / mid).write_text("---\ntitle: New\n---\n", encoding="utf-8", newline="\n")
    real_read = sync.read_member
    real_reconcile = readindex.reconcile
    indexes = []

    def changing_read(root, mid):
        result = real_read(root, mid)
        if mid in new_ids:
            with (root / mid).open("a", encoding="utf-8", newline="\n") as stream:
                stream.write("Changed during read\n")
        return result

    def capture(index):
        indexes.append(index)
        result = real_reconcile(index)
        assert set(result.unsettled) == set(new_ids)
        assert index.connection.execute("SELECT id FROM members WHERE id IN (?, ?)", new_ids).fetchall() == []
        return result

    flags = [flag, *(["--json"] if json_mode else [])]
    with monkeypatch.context() as patch:
        patch.setattr(sync, "read_member", changing_read)
        patch.setattr(readindex, "reconcile", capture)
        result = invoke(initialized_workspace, *flags)
    assert result.exit_code == exit_codes.GENERIC, result.output
    assert result.stdout == ""
    assert result.stderr.startswith("Error: ")
    assert all(mid in result.stderr for mid in new_ids)
    assert len(indexes) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        indexes[0].connection.execute("SELECT 1")
    database = indexes[0].db_path
    assert list(database.parent.iterdir()) == [database]

    settled = invoke(initialized_workspace, *flags)
    assert settled.exit_code == 0, settled.output
    if json_mode:
        payload = json.loads(settled.stdout)
        assert payload["error"] is None
        if flag == "--verify":
            assert payload["drift"] == []
    else:
        assert "drift: none" in settled.stdout
    with sqlite3.connect(database) as conn:
        assert set(conn.execute("SELECT id FROM members WHERE id IN (?, ?)", new_ids)) == {(mid,) for mid in new_ids}
    conn.close()


def test_verify_and_rebuild_together_is_a_usage_error(initialized_workspace: Path) -> None:
    result = invoke(initialized_workspace, "--verify", "--rebuild")
    assert result.exit_code == exit_codes.GENERIC
    assert "cannot be combined" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("error_type", [ReadIndexBusy, ReadIndexUnavailable, OSError, sqlite3.OperationalError])
@pytest.mark.parametrize("flag", ["--verify", "--rebuild"])
def test_maintenance_error_uses_shared_helper(
    initialized_workspace: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception], flag: str
) -> None:
    from graph_works_cli.util_cli import read_index as module

    def boom(layout, *, verify: bool, rebuild: bool):
        raise error_type("maintenance refused")

    monkeypatch.setattr(module, "run_read_index", boom)
    result = invoke(initialized_workspace, flag, "--json")
    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "Error: maintenance refused" in result.stderr


@pytest.mark.parametrize("json_mode", [False, True])
def test_corrupt_inspect_reports_error_without_writes_or_failure(initialized_workspace: Path, json_mode: bool) -> None:
    assert invoke(initialized_workspace, "--rebuild").exit_code == 0
    payload = json.loads(invoke(initialized_workspace, "--json").stdout)
    database = Path(payload["db_path"])
    database.write_bytes(b"corrupt database")
    before = set(initialized_workspace.rglob("*"))
    result = invoke(initialized_workspace, *(["--json"] if json_mode else []))
    assert result.exit_code == 0, result.output
    if json_mode:
        assert json.loads(result.stdout)["error"]
    else:
        assert "error:" in result.stdout
    assert database.read_bytes() == b"corrupt database"
    assert set(initialized_workspace.rglob("*")) == before


def test_human_report_formats_utc_and_all_optional_facts(
    initialized_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from graph_works_cli.util_cli import read_index as module

    real = module.run_read_index
    monkeypatch.setattr(
        module,
        "run_read_index",
        lambda layout, **kw: dataclasses.replace(
            real(layout, **kw),
            enabled=True,
            backend="index",
            backend_reason=None,
            stored_fingerprint="stale",
            generation=7,
            last_reconcile_ns=1_500_000_000,
            schema_version="1",
            projection_version="2",
            okf_io_version="0.2.0",
            tables={"tags": 3, "members": 2},
            kinds={"z": 1, "a": 2},
            unreadable_files=4,
            rebuilt_reason="missing",
            elapsed_s=0.5,
            parsed=2,
        ),
    )
    result = invoke(initialized_workspace, "--rebuild")
    assert result.exit_code == 0, result.output
    for line in [
        "enabled: true",
        "backend: index",
        "generation: 7",
        "last reconcile: 1970-01-01T00:00:01.500000+00:00",
        "schema: 1",
        "projection: 2",
        "okf_io: 0.2.0",
        "fingerprint: stale",
        "table members: 2",
        "table tags: 3",
        "kind a: 2",
        "kind z: 1",
        "unreadable files: 4",
        "rebuilt: missing",
        "rebuild: 0.5s, 2 files parsed",
    ]:
        assert line in result.stdout


def test_json_workspace_refusal_uses_command_identity(tmp_path: Path) -> None:
    result = invoke(tmp_path, "--json")
    assert result.exit_code == exit_codes.NOT_INITIALIZED
    payload = json.loads(result.stdout)
    assert payload["error"]["command"] == "util read-index"


def test_disabled_inspect_reports_configuration_preference(initialized_workspace: Path) -> None:
    result = runner.invoke(
        app, ["config", "set", "read_index.enabled", "false", "--workspace", str(initialized_workspace)]
    )
    assert result.exit_code == 0, result.output
    result = invoke(initialized_workspace)
    assert result.exit_code == 0, result.output
    assert "enabled: false" in result.stdout
    assert "backend: bundle (disabled)" in result.stdout

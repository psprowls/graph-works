"""Inspection of installed work-lane schemas against the packaged seeds."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace import work_schemas as ws
from graph_works_core.workspace.layout import WorkspaceLayout

TODAY = date(2026, 10, 5)
BASE = "schema/_base.schema.json"


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    return apply_init(plan_init(tmp_path / "workspace", today=TODAY, topic="Schemas")).layout


def _write(layout: WorkspaceLayout, relative: str, data: bytes) -> Path:
    path = ws.declarations_dir_for(layout) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _drop_provenance(layout: WorkspaceLayout) -> None:
    (ws.declarations_dir_for(layout) / ws.PROVENANCE_RELATIVE).unlink(missing_ok=True)


def _record_current_provenance(layout: WorkspaceLayout) -> None:
    digests = {key: hashlib.sha256(value).hexdigest() for key, value in ws.packaged_schemas().items()}
    _write(layout, ws.PROVENANCE_RELATIVE, ws.render_provenance(digests))


def test_packaged_schemas_are_the_eight_work_schemas() -> None:
    packaged = ws.packaged_schemas()
    assert len(packaged) == 8
    assert BASE in packaged
    assert all(key.startswith("schema/") and key.endswith(".schema.json") for key in packaged)


def test_declarations_dir_matches_the_validation_gate(layout: WorkspaceLayout) -> None:
    assert ws.declarations_dir_for(layout) == layout.config_dir


def test_current_without_provenance_is_not_drift(layout: WorkspaceLayout) -> None:
    _drop_provenance(layout)
    inspection = ws.inspect_work_schemas(layout)
    assert inspection.drifted == ()
    assert inspection.recorded is None


def test_old_base_with_recorded_digest_is_recognized(layout: WorkspaceLayout) -> None:
    old = b'{"old": true}\n'
    _write(layout, BASE, old)
    digests = {key: hashlib.sha256(value).hexdigest() for key, value in ws.packaged_schemas().items()}
    digests[BASE] = hashlib.sha256(old).hexdigest()
    _write(layout, ws.PROVENANCE_RELATIVE, ws.render_provenance(digests))
    [state] = ws.inspect_work_schemas(layout).drifted
    assert (state.relative, state.state) == (BASE, "recognized")


def test_local_edit_with_provenance_is_edited(layout: WorkspaceLayout) -> None:
    digests = {key: hashlib.sha256(value).hexdigest() for key, value in ws.packaged_schemas().items()}
    _write(layout, ws.PROVENANCE_RELATIVE, ws.render_provenance(digests))
    _write(layout, BASE, b"{}\n")
    [state] = ws.inspect_work_schemas(layout).drifted
    assert state.state == "edited"


def test_differing_copy_without_provenance_is_unrecorded(layout: WorkspaceLayout) -> None:
    _drop_provenance(layout)
    _write(layout, BASE, b"{}\n")
    [state] = ws.inspect_work_schemas(layout).drifted
    assert state.state == "unrecorded"


def test_crlf_copy_is_drift_not_current(layout: WorkspaceLayout) -> None:
    _drop_provenance(layout)
    _write(layout, BASE, ws.packaged_schemas()[BASE].replace(b"\n", b"\r\n"))
    [state] = ws.inspect_work_schemas(layout).drifted
    assert state.state == "unrecorded"


def test_missing_file_is_missing(layout: WorkspaceLayout) -> None:
    (ws.declarations_dir_for(layout) / BASE).unlink()
    [state] = ws.inspect_work_schemas(layout).drifted
    assert state.state == "missing" and state.installed is None


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_symlink_is_unsafe(layout: WorkspaceLayout, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_bytes(ws.packaged_schemas()[BASE])
    path = ws.declarations_dir_for(layout) / BASE
    path.unlink()
    path.symlink_to(target)
    [state] = ws.inspect_work_schemas(layout).drifted
    assert state.state == "unsafe"


def test_malformed_provenance_is_reported_and_not_trusted(layout: WorkspaceLayout) -> None:
    _write(layout, ws.PROVENANCE_RELATIVE, b"{not json")
    _write(layout, BASE, b"{}\n")
    inspection = ws.inspect_work_schemas(layout)
    assert inspection.recorded is None
    assert inspection.provenance_error is not None
    assert [s.state for s in inspection.drifted] == ["unrecorded"]


def test_relocated_config_dir_is_inspected(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "workspace.yaml").write_text(
        "topic: Relocated\ninitialized_at: '2026-10-05'\nlayout:\n  config_dir: .config-gw\n",
        encoding="utf-8",
        newline="",
    )
    layout = apply_init(plan_init(root, today=TODAY, topic="Relocated")).layout
    assert ws.declarations_dir_for(layout) == layout.config_dir
    assert layout.config_dir.name == ".config-gw"
    assert ws.inspect_work_schemas(layout).drifted == ()


def test_custom_schema_files_are_ignored(layout: WorkspaceLayout) -> None:
    _write(layout, "schema/Custom.schema.json", b"{}\n")
    assert all(s.relative != "schema/Custom.schema.json" for s in ws.inspect_work_schemas(layout).files)


def test_render_provenance_round_trips(layout: WorkspaceLayout) -> None:
    digests = {BASE: "0" * 64}
    data = json.loads(ws.render_provenance(digests))
    assert data["format"] == 1 and data["package"] == "work-tracker-okf" and data["files"] == digests


def test_legacy_bundle_declarations_are_inspected(layout: WorkspaceLayout) -> None:
    layout.manifest_path.unlink()
    (layout.config_dir / "schema").rename(layout.bundle_dir / "schema")
    inspection = ws.inspect_work_schemas(layout)
    assert inspection.declarations_dir == layout.bundle_dir
    assert inspection.drifted == ()
    assert all(state.path.parent == layout.bundle_dir / "schema" for state in inspection.files)


def test_missing_configured_directory_does_not_select_bundle_schemas(layout: WorkspaceLayout) -> None:
    (layout.config_dir / "schema").rename(layout.bundle_dir / "schema")
    inspection = ws.inspect_work_schemas(layout)
    assert inspection.declarations_dir == layout.config_dir
    assert len(inspection.drifted) == 8
    assert all(state.state == "missing" for state in inspection.files)


def test_absent_manifest_without_legacy_declarations_uses_config_dir(layout: WorkspaceLayout) -> None:
    layout.manifest_path.unlink()
    (layout.config_dir / "schema").rename(layout.root / "saved-schema")
    assert ws.declarations_dir_for(layout) == layout.config_dir


def test_custom_only_bundle_schemas_do_not_identify_legacy_work_declarations(layout: WorkspaceLayout) -> None:
    layout.manifest_path.unlink()
    (layout.config_dir / "schema").rename(layout.root / "saved-schema")
    directory = layout.bundle_dir / "schema"
    directory.mkdir()
    (directory / "Custom.schema.json").write_bytes(b"{}\n")
    assert ws.declarations_dir_for(layout) == layout.config_dir


def test_recovery_command_preserves_workspace_argument(tmp_path: Path) -> None:
    layout = apply_init(plan_init(tmp_path / "workspace with spaces;$value", today=TODAY, topic="Q")).layout
    assert shlex.split(ws.refresh_command(layout)) == [
        "gw",
        "config",
        "sync",
        "--schemas",
        "--workspace",
        str(layout.root),
    ]


def test_windows_recovery_command_quotes_workspace_argument(
    layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    monkeypatch.setattr(sys, "platform", "win32")
    found = replace(layout, root=Path("C:/Workspace With Spaces"))
    assert ws.refresh_command(found) == f'gw config sync --schemas --workspace "{found.root}"'


def test_directory_schema_target_is_unsafe(layout: WorkspaceLayout) -> None:
    path = ws.declarations_dir_for(layout) / BASE
    path.unlink()
    path.mkdir()
    [state] = ws.inspect_work_schemas(layout).drifted
    assert state.state == "unsafe" and state.installed is None


def test_no_findings_when_current(layout: WorkspaceLayout) -> None:
    assert ws.drift_findings(layout) == ()
    assert ws.drift_detail(layout) is None


def test_stale_file_yields_one_named_error_and_recovery(layout: WorkspaceLayout) -> None:
    _record_current_provenance(layout)
    _write(layout, BASE, b"{}\n")
    [finding] = ws.drift_findings(layout)
    assert finding.code == ws.SCHEMA_DRIFT and finding.severity == "error"
    assert finding.path == f"{layout.config_dir.relative_to(layout.root).as_posix()}/{BASE}"
    assert "gw config sync --schemas --workspace" in finding.message
    detail = ws.drift_detail(layout)
    assert detail is not None and BASE in detail and "edited" in detail


def test_missing_file_uses_the_missing_code(layout: WorkspaceLayout) -> None:
    (ws.declarations_dir_for(layout) / BASE).unlink()
    [finding] = ws.drift_findings(layout)
    assert finding.code == ws.SCHEMA_MISSING


def test_every_drifted_file_is_named(layout: WorkspaceLayout) -> None:
    _record_current_provenance(layout)
    _write(layout, BASE, b"{}\n")
    _write(layout, "schema/Bug.schema.json", b"{}\n")
    findings = ws.drift_findings(layout)
    assert len(findings) == 2
    assert {finding.path for finding in findings} == {
        f"{layout.config_dir.relative_to(layout.root).as_posix()}/{BASE}",
        f"{layout.config_dir.relative_to(layout.root).as_posix()}/schema/Bug.schema.json",
    }


@pytest.mark.parametrize("operation", ["stat", "read_bytes"])
def test_unreadable_schema_is_unsafe(layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    path = ws.declarations_dir_for(layout) / BASE
    original = getattr(Path, operation)

    def denied(candidate: Path, *args: object, **kwargs: object) -> object:
        if candidate == path:
            raise PermissionError("schema access denied")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, operation, denied)
    [state] = ws.inspect_work_schemas(layout).drifted
    assert state.state == "unsafe" and state.installed is None
    assert "schema access denied" in state.detail


@pytest.mark.parametrize(
    "invalid",
    [
        {"format": True, "package": "work-tracker-okf", "files": {BASE: "0" * 64}},
        {"format": 1.0, "package": "work-tracker-okf", "files": {BASE: "0" * 64}},
        {"format": 1, "package": "work-tracker-okf", "files": {BASE: "x" * 64}},
        {"format": 1, "package": "other", "files": {BASE: "0" * 64}},
        {"format": 1, "package": "work-tracker-okf", "files": []},
        [],
    ],
)
def test_invalid_provenance_metadata_is_not_trusted(layout: WorkspaceLayout, invalid: object) -> None:
    raw = json.dumps(invalid).encode("utf-8")
    _write(layout, ws.PROVENANCE_RELATIVE, raw)
    _write(layout, BASE, b"{}\n")
    inspection = ws.inspect_work_schemas(layout)
    assert inspection.provenance_bytes == raw
    assert inspection.recorded is None
    assert inspection.provenance_error is not None
    assert [state.state for state in inspection.drifted] == ["unrecorded"]


@pytest.mark.skipif(os.name == "nt", reason="FIFO creation is POSIX-only")
@pytest.mark.parametrize("kind", ["fifo", "fifo-symlink", "directory", "symlink-parent"])
def test_unsafe_provenance_is_rejected_before_open(
    layout: WorkspaceLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path = ws.declarations_dir_for(layout) / ws.PROVENANCE_RELATIVE
    path.unlink(missing_ok=True)
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "fifo-symlink":
        target = tmp_path / "outside.fifo"
        os.mkfifo(target)
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    else:
        parent = path.parent
        target = tmp_path / "outside-schema"
        parent.rename(target)
        os.mkfifo(target / path.name)
        parent.symlink_to(target, target_is_directory=True)
    real_open = Path.open

    def no_unsafe_open(self: Path, *args: object, **kwargs: object) -> object:
        if self == path:
            pytest.fail("unsafe provenance opened before type/parent guard")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_unsafe_open)
    inspection = ws.inspect_work_schemas(layout)
    assert inspection.recorded is None
    assert inspection.provenance_bytes is None
    assert inspection.provenance_error is not None

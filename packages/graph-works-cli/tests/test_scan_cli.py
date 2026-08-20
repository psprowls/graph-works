"""`gw scan` process-boundary modes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from code_wiki_okf.config import ConfigError
from code_wiki_okf.entities.lanes import SyncSummary
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import scan as scan_module
from graph_works_core.scan.commands import ScanResult, StructuralSummary
from graph_works_core.scan.scan_contract import ApplyResult, ScanWorklist, worklist_payload
from graph_works_core.workspace.errors import ScanError
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    """Create the smallest real initialized workspace for CLI boundary tests."""
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0
    return root


@pytest.mark.parametrize(
    "args",
    (
        ("--emit-worklist", "--apply"),
        ("--emit-worklist", "--no-narrate"),
        ("--apply", "--no-narrate"),
    ),
)
def test_scan_rejects_conflicting_modes_before_workspace_resolution(args: tuple[str, ...]) -> None:
    """A mode-selection bug must not be able to choose an arbitrary branch."""
    result = runner.invoke(app, ["scan", *args])

    assert result.exit_code == 2
    assert "mutually exclusive" in result.stderr


@pytest.mark.parametrize(
    "args",
    (("--apply",), ("--apply", "--results-dir", "results"), ("--apply", "--short-head", "abc123")),
)
def test_apply_requires_both_process_handoff_inputs(args: tuple[str, ...]) -> None:
    """An incomplete handoff must be rejected rather than guessed."""
    result = runner.invoke(app, ["scan", *args])

    assert result.exit_code == 2
    assert "requires both --results-dir and --short-head" in result.stderr


@pytest.mark.parametrize(
    ("args", "message"),
    (
        (("--results-dir", "results"), "--results-dir is only valid with --apply"),
        (("--short-head", "abc123"), "--short-head is only valid with --apply"),
        (("--emit-worklist", "--results-dir", "results"), "--results-dir is only valid with --apply"),
        (("--emit-worklist", "--short-head", "abc123"), "--short-head is only valid with --apply"),
        (("--emit-worklist", "--json"), "--json is only valid in normal mode"),
        (
            ("--apply", "--results-dir", "results", "--short-head", "abc123", "--json"),
            "--json is only valid in normal mode",
        ),
    ),
)
def test_scan_rejects_mode_specific_inputs_outside_their_mode(args: tuple[str, ...], message: str) -> None:
    """Inert handoff flags must not silently change the normal scan path."""
    result = runner.invoke(app, ["scan", *args])

    assert result.exit_code == 2
    assert message in result.stderr


@pytest.mark.parametrize(("no_narrate", "expected_narrate"), ((False, True), (True, False)))
def test_normal_scan_applies_and_translates_the_narration_flag(
    monkeypatch: pytest.MonkeyPatch,
    initialized_workspace: Path,
    no_narrate: bool,
    expected_narrate: bool,
) -> None:
    """A wrong `dry_run` or `narrate` argument would skip required scan work."""
    calls: list[dict[str, object]] = []

    async def fake_run_scan(*args: object, **kwargs: object) -> ScanResult:
        calls.append({"layout": args[0], "config": args[1], **kwargs})
        return ScanResult(structural=StructuralSummary(), worklist=ScanWorklist(short_head="abc123"))

    monkeypatch.setattr(scan_module, "run_scan", fake_run_scan)
    args = ["scan", "--json", "--workspace", str(initialized_workspace)]
    if no_narrate:
        args.append("--no-narrate")

    result = runner.invoke(app, args)

    assert result.exit_code == 0
    assert calls[0]["dry_run"] is False
    assert calls[0]["narrate"] is expected_narrate
    assert json.loads(result.stdout)["short_head"] == "abc123"


@pytest.mark.parametrize("no_narrate", (False, True))
@pytest.mark.parametrize("json_output", (False, True))
def test_normal_scan_exits_one_for_structural_catalog_declines_in_every_mode(
    monkeypatch: pytest.MonkeyPatch,
    initialized_workspace: Path,
    no_narrate: bool,
    json_output: bool,
) -> None:
    """Normal and structural-only CLI runs must reject the same catalog error."""

    async def fake_run_scan(*args: object, **kwargs: object) -> ScanResult:
        return ScanResult(
            structural=StructuralSummary(
                entities=SyncSummary(catalog_declined=(("packages/broken.md", "parse-error"),))
            ),
            worklist=ScanWorklist(short_head="abc123"),
            errors=("packages/broken.md: parse-error",),
        )

    monkeypatch.setattr(scan_module, "run_scan", fake_run_scan)
    args = ["scan", "--workspace", str(initialized_workspace)]
    if json_output:
        args.append("--json")
    if no_narrate:
        args.append("--no-narrate")

    result = runner.invoke(app, args)

    assert result.exit_code == exit_codes.GENERIC
    if json_output:
        assert json.loads(result.stdout)["entity_errors"] == ["packages/broken.md: parse-error"]
    else:
        assert result.stdout == ""
    assert result.stderr == "Error: scan completed with entity errors\n"


def test_emit_writes_only_canonical_artifacts_and_resets_canonical_results(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A wrong reset target could delete an external agent's unrelated files."""
    layout = scan_module.resolve_workspace(str(initialized_workspace))
    cache = scan_module.scan_cache_dir(layout)
    results = scan_module.scan_results_dir(layout)
    results.mkdir(parents=True)
    (results / "stale.json").write_text("old", encoding="utf-8")
    nested = results / "nested"
    nested.mkdir()
    (nested / "stale.json").write_text("old", encoding="utf-8")
    preserved = cache / "keep.txt"
    preserved.parent.mkdir(parents=True, exist_ok=True)
    preserved.write_text("keep", encoding="utf-8")
    calls: list[dict[str, object]] = []

    async def fake_build_worklist(*args: object, **kwargs: object) -> tuple[ScanWorklist, StructuralSummary]:
        calls.append({"layout": args[0], "config": args[1], **kwargs})
        return ScanWorklist(short_head="abc123"), StructuralSummary(entities=SyncSummary(written=("packages/demo",)))

    monkeypatch.setattr(scan_module, "build_scan_worklist", fake_build_worklist)

    result = runner.invoke(app, ["scan", "--emit-worklist", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert calls[0]["dry_run"] is False
    assert list(results.iterdir()) == []
    assert preserved.read_text(encoding="utf-8") == "keep"
    payload = json.loads(result.stdout)
    assert payload["worklist_path"] == str(cache / "worklist.json")
    assert payload["briefs_dir"] == str(cache / "briefs")
    assert payload["results_dir"] == str(results)


def test_emit_replaces_a_canonical_results_symlink_without_touching_its_target(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, tmp_path: Path
) -> None:
    """A canonical leaf symlink must not turn reset into external deletion."""
    layout = scan_module.resolve_workspace(str(initialized_workspace))
    results = scan_module.scan_results_dir(layout)
    external = tmp_path / "external-results"
    external.mkdir()
    sentinel = external / "sentinel.json"
    sentinel.write_text("keep", encoding="utf-8")
    results.parent.mkdir(parents=True, exist_ok=True)
    results.symlink_to(external, target_is_directory=True)

    async def fake_build_worklist(*args: object, **kwargs: object) -> tuple[ScanWorklist, StructuralSummary]:
        return ScanWorklist(short_head="abc123"), StructuralSummary()

    monkeypatch.setattr(scan_module, "build_scan_worklist", fake_build_worklist)

    result = runner.invoke(app, ["scan", "--emit-worklist", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert results.is_dir()
    assert not results.is_symlink()
    assert list(results.iterdir()) == []


def test_apply_refuses_a_stale_short_head_before_apply(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A stale result directory must never be applied to a newer worklist."""
    layout = scan_module.resolve_workspace(str(initialized_workspace))
    worklist_path = scan_module.scan_cache_dir(layout) / scan_module.WORKLIST_FILENAME
    worklist_path.parent.mkdir(parents=True)
    worklist_path.write_text(json.dumps(worklist_payload(ScanWorklist(short_head="expected"))), encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(scan_module, "apply_scan_worklist", calls.append)
    monkeypatch.setattr(
        scan_module,
        "load_config",
        lambda _bundle_root: (_ for _ in ()).throw(AssertionError("stale apply must not read bundle configuration")),
    )

    result = runner.invoke(
        app,
        [
            "scan",
            "--apply",
            "--results-dir",
            "results",
            "--short-head",
            "wrong",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == exit_codes.STALE
    assert calls == []
    assert "does not match" in result.stderr


def test_apply_maps_unsupported_worklist_schema_before_applying(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A newer or older wire contract must fail closed instead of corrupting pages."""
    layout = scan_module.resolve_workspace(str(initialized_workspace))
    worklist_path = scan_module.scan_cache_dir(layout) / scan_module.WORKLIST_FILENAME
    worklist_path.parent.mkdir(parents=True)
    worklist_path.write_text('{"schema_version": 0}', encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(scan_module, "apply_scan_worklist", calls.append)

    result = runner.invoke(
        app,
        [
            "scan",
            "--apply",
            "--results-dir",
            "results",
            "--short-head",
            "abc123",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert calls == []
    assert "unsupported worklist schema" in result.stderr


def test_apply_passes_the_validated_worklist_object_to_core(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Replacing the cache file cannot swap the worklist after validation."""
    worklist = ScanWorklist(short_head="abc123")
    calls: list[dict[str, Any]] = []

    def fake_apply_scan_worklist(**kwargs: Any) -> ApplyResult:
        calls.append(kwargs)
        return ApplyResult()

    monkeypatch.setattr(scan_module, "load_worklist", lambda _path: worklist)
    monkeypatch.setattr(scan_module, "apply_scan_worklist", fake_apply_scan_worklist)

    result = runner.invoke(
        app,
        [
            "scan",
            "--apply",
            "--results-dir",
            "results",
            "--short-head",
            "abc123",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == 0
    assert calls[0]["worklist"] is worklist


def test_entity_errors_stay_in_apply_json_and_fail_the_command(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A partial apply must remain machine-readable while returning failure."""
    layout = scan_module.resolve_workspace(str(initialized_workspace))
    worklist_path = scan_module.scan_cache_dir(layout) / scan_module.WORKLIST_FILENAME
    worklist_path.parent.mkdir(parents=True)
    worklist_path.write_text(json.dumps(worklist_payload(ScanWorklist(short_head="abc123"))), encoding="utf-8")
    calls: list[dict[str, Any]] = []

    def fake_apply_scan_worklist(**kwargs: Any) -> ApplyResult:
        calls.append(kwargs)
        return ApplyResult(entity_errors=("packages/demo: declined",))

    monkeypatch.setattr(scan_module, "apply_scan_worklist", fake_apply_scan_worklist)

    result = runner.invoke(
        app,
        [
            "scan",
            "--apply",
            "--results-dir",
            "results",
            "--short-head",
            "abc123",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == exit_codes.GENERIC
    assert calls[0]["dry_run"] is False
    assert json.loads(result.stdout) == {
        "narrated": 0,
        "sections_filled": 0,
        "stamped": 0,
        "entity_errors": ["packages/demo: declined"],
    }


def test_apply_reports_a_missing_worklist_instead_of_a_traceback(initialized_workspace: Path) -> None:
    """Applying before any emit is ordinary operator error and must read as a message."""
    result = runner.invoke(
        app,
        [
            "scan",
            "--apply",
            "--results-dir",
            "results",
            "--short-head",
            "abc123",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == exit_codes.GENERIC
    assert "Error: " in result.stderr


def test_apply_reports_a_failed_apply_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A mid-apply failure leaves pages half-written; it must surface, not raise."""
    layout = scan_module.resolve_workspace(str(initialized_workspace))
    worklist_path = scan_module.scan_cache_dir(layout) / scan_module.WORKLIST_FILENAME
    worklist_path.parent.mkdir(parents=True)
    worklist_path.write_text(json.dumps(worklist_payload(ScanWorklist(short_head="abc123"))), encoding="utf-8")

    def fail(**_kwargs: Any) -> ApplyResult:
        raise ScanError("results directory is incomplete")

    monkeypatch.setattr(scan_module, "apply_scan_worklist", fail)

    result = runner.invoke(
        app,
        [
            "scan",
            "--apply",
            "--results-dir",
            "results",
            "--short-head",
            "abc123",
            "--workspace",
            str(initialized_workspace),
        ],
    )

    assert result.exit_code == exit_codes.GENERIC
    assert "Error: results directory is incomplete" in result.stderr


def test_scan_reports_unreadable_configuration_before_running(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Normal mode reads configuration first; its failure must not look like a scan failure."""

    def fail(_bundle_root: object) -> object:
        raise ConfigError("config.yaml is malformed")

    monkeypatch.setattr(scan_module, "load_config", fail)
    monkeypatch.setattr(
        scan_module, "run_scan", lambda *_args, **_kwargs: pytest.fail("an unreadable config must not start a scan")
    )

    result = runner.invoke(app, ["scan", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert "Error: config.yaml is malformed" in result.stderr


def test_emit_fails_when_the_catalog_declined_an_entity(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A declined entity means the emitted worklist is incomplete; exiting zero would hide that."""

    async def fake_build_worklist(*_args: object, **_kwargs: object) -> tuple[ScanWorklist, StructuralSummary]:
        return ScanWorklist(short_head="abc123"), StructuralSummary(
            entities=SyncSummary(catalog_declined=(("packages/demo", "unadmitted"),))
        )

    monkeypatch.setattr(scan_module, "build_scan_worklist", fake_build_worklist)

    result = runner.invoke(app, ["scan", "--emit-worklist", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert "scan emitted entity errors" in result.stderr
    assert json.loads(result.stdout)["short_head"] == "abc123"


def test_scan_reports_a_failed_run_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A scan that raises mid-walk must name the reason rather than crash the CLI."""

    async def fail(*_args: object, **_kwargs: object) -> ScanResult:
        raise ScanError("repository walk failed")

    monkeypatch.setattr(scan_module, "run_scan", fail)

    result = runner.invoke(app, ["scan", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert "Error: repository walk failed" in result.stderr

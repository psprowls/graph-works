"""`gw util platform` — the read-only platform report.

The default run is the interesting one: it must work on a machine with no
workspace at all, because a user asking "what am I actually getting here?" is
exactly the caller who has not got a working workspace.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from graph_works_cli.cli import app
from graph_works_cli.util_cli import platform as platform_module
from graph_works_core.util.platform import Capability, PlatformReport, ProbeResult
from typer.testing import CliRunner

runner = CliRunner()


def _payload(result: Any) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(result.stdout))


def test_the_default_run_needs_no_workspace(tmp_path: Path, monkeypatch) -> None:
    """Acceptance property 3, at the CLI boundary."""
    monkeypatch.chdir(tmp_path)

    def _forbidden(_workspace: str) -> object:
        raise AssertionError("the default run must not resolve a workspace")

    monkeypatch.setattr(platform_module, "resolve_workspace", _forbidden)
    result = runner.invoke(app, ["util", "platform"])

    assert result.exit_code == 0, result.output


def test_the_json_mode_carries_every_field() -> None:
    result = runner.invoke(app, ["util", "platform", "--json"])

    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert payload["schema_version"] == 1
    assert payload["platform"]
    assert payload["python"]
    assert payload["probes"] == []
    names = [c["name"] for c in payload["capabilities"]]
    assert names == ["durability-tier", "dispatch-backend", "file-lock", "process-control"]
    first = payload["capabilities"][0]
    assert set(first) == {"name", "value", "status", "detail", "guarantees", "provider"}
    assert payload["unavailable"] == [c["name"] for c in payload["capabilities"] if c["status"] == "unavailable"]


def test_an_unavailable_capability_still_exits_zero(monkeypatch) -> None:
    """Acceptance property 5. This is a report, not a check."""
    monkeypatch.setattr(
        platform_module,
        "build_report",
        lambda **_kwargs: PlatformReport(
            schema_version=1,
            platform="win32",
            python="3.12.7",
            capabilities=(Capability("durability-tier", "unavailable", "unavailable", "no fcntl", (), "m"),),
            probes=(),
        ),
    )
    result = runner.invoke(app, ["util", "platform"])

    assert result.exit_code == 0, result.output
    assert "unavailable" in result.stdout


def _disagreeing_report() -> PlatformReport:
    return PlatformReport(
        schema_version=1,
        platform="win32",
        python="3.12.7",
        capabilities=(Capability("dispatch-backend", "workflow-orca", "available", "resolved", (), "m"),),
        probes=(ProbeResult("dispatch-backend", "unavailable", "orca is not on PATH", agrees_with_declared=False),),
    )


def test_a_disagreement_is_visible_in_the_human_view(monkeypatch) -> None:
    """Acceptance property 4. Collapsing the two into one value destroys the
    single most useful thing this verb can tell a Windows user."""
    monkeypatch.setattr(platform_module, "build_report", lambda **_kwargs: _disagreeing_report())
    result = runner.invoke(app, ["util", "platform"])

    assert result.exit_code == 0, result.output
    assert "workflow-orca" in result.stdout
    assert "orca is not on PATH" in result.stdout
    assert "disagrees" in result.stdout


def test_a_disagreement_is_visible_in_json(monkeypatch) -> None:
    monkeypatch.setattr(platform_module, "build_report", lambda **_kwargs: _disagreeing_report())
    result = runner.invoke(app, ["util", "platform", "--json"])

    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert payload["capabilities"][0]["value"] == "workflow-orca"
    assert payload["probes"][0]["agrees_with_declared"] is False


def test_probing_end_to_end_against_a_real_workspace(tmp_path: Path) -> None:
    """No monkeypatching of `build_report` or `resolve_workspace`: a fully
    real run against a real, minimal, initialized workspace, proving the
    real probe path renders through `_human()`."""
    root = tmp_path / "works"
    bootstrap_result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert bootstrap_result.exit_code == 0, bootstrap_result.output

    result = runner.invoke(app, ["util", "platform", "--probe", "--workspace", str(root)])

    assert result.exit_code == 0, result.output
    assert "probed:" in result.stdout


def test_probing_resolves_a_workspace(tmp_path: Path, monkeypatch) -> None:
    seen: list[str] = []

    def _resolve(workspace: str) -> object:
        seen.append(workspace)
        return object()

    monkeypatch.setattr(platform_module, "resolve_workspace", _resolve)
    monkeypatch.setattr(platform_module, "build_report", lambda **_kwargs: _disagreeing_report())
    result = runner.invoke(app, ["util", "platform", "--probe", "--workspace", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert seen == [str(tmp_path)]


def test_guarantees_are_rendered_under_their_capability(monkeypatch) -> None:
    """A tier name with no contract beside it surfaces nothing a user can act on."""
    monkeypatch.setattr(
        platform_module,
        "build_report",
        lambda **_kwargs: PlatformReport(
            schema_version=1,
            platform="darwin",
            python="3.12.7",
            capabilities=(
                Capability(
                    "durability-tier", "posix-strong", "available", "ok", ("every effect lands or none does",), "m"
                ),
            ),
            probes=(),
        ),
    )
    result = runner.invoke(app, ["util", "platform"])

    assert "every effect lands or none does" in result.stdout


def test_no_guarantee_line_when_guarantees_is_empty(monkeypatch) -> None:
    """An empty `guarantees` tuple must render zero `guarantee:` lines."""
    monkeypatch.setattr(
        platform_module,
        "build_report",
        lambda **_kwargs: PlatformReport(
            schema_version=1,
            platform="darwin",
            python="3.12.7",
            capabilities=(Capability("durability-tier", "posix-strong", "available", "ok", (), "m"),),
            probes=(),
        ),
    )
    result = runner.invoke(app, ["util", "platform"])

    assert result.exit_code == 0, result.output
    assert "guarantee:" not in result.stdout


def test_a_probe_appears_only_under_its_own_capability(monkeypatch) -> None:
    """A report with two capabilities and one probe must not leak the probe
    line into the capability block it does not belong to."""
    monkeypatch.setattr(
        platform_module,
        "build_report",
        lambda **_kwargs: PlatformReport(
            schema_version=1,
            platform="win32",
            python="3.12.7",
            capabilities=(
                Capability("durability-tier", "posix-strong", "available", "ok", (), "m"),
                Capability("dispatch-backend", "workflow-orca", "available", "resolved", (), "m"),
            ),
            probes=(ProbeResult("durability-tier", "available", "confirmed", agrees_with_declared=True),),
        ),
    )
    result = runner.invoke(app, ["util", "platform"])

    assert result.exit_code == 0, result.output
    assert result.stdout.count("probed:") == 1

    _, _, after_durability = result.stdout.partition("durability-tier:")
    durability_block, _, dispatch_block = after_durability.partition("dispatch-backend:")

    assert "probed:" in durability_block
    assert "probed:" not in dispatch_block

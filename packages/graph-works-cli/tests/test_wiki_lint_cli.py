"""`gw wiki lint` — fixed lint orchestration at the CLI boundary."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from code_wiki_okf.config import ConfigError
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import lint as lint_module
from graph_works_core.lint_drift.lint import LaneReport, LintReport, ProposalBacklog, SemanticFinding
from okf_io import Finding, Report
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    """Create the smallest real initialized workspace for CLI boundary tests."""
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0
    return root


def test_lint_passes_one_captured_utc_date_to_the_typed_core_call(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A separate local date could make one lint run internally inconsistent."""
    calls: list[dict[str, object]] = []
    expected_now = datetime(2026, 8, 18, 9, 30, tzinfo=UTC)
    report = LintReport(
        mechanical=(
            LaneReport(
                "wiki",
                Report((Finding("links.broken", "warn", "missing target", "§5", "concepts/a.md", 7),)),
            ),
        ),
    )

    class Clock:
        @classmethod
        def now(cls, tz: object) -> datetime:
            assert tz is UTC
            return expected_now

    async def fake_run_lint(*args: object, **kwargs: object) -> LintReport:
        calls.append({"layout": args[0], "config": args[1], **kwargs})
        return report

    monkeypatch.setattr(lint_module, "datetime", Clock)
    monkeypatch.setattr(lint_module, "run_lint", fake_run_lint)

    result = runner.invoke(app, ["wiki", "lint", "--workspace", str(initialized_workspace)])

    layout = lint_module.resolve_workspace(str(initialized_workspace))
    assert result.exit_code == 0
    assert calls == [
        {
            "layout": layout,
            "config": lint_module.load_config(
                layout.bundle_dir,
                config_path=layout.manifest_path,
                graph_dir=layout.cache_dir,
                declarations_dir=layout.config_dir,
            ),
            "today": date(2026, 8, 18),
            "repo_root": layout.repo_root,
        }
    ]
    assert result.stdout == report.render() + "\n"
    assert result.stderr == ""


def test_lint_json_has_only_the_documented_nested_fields(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """A whole-dataclass serializer would leak internal lint fields into the wire format."""
    report = LintReport(
        mechanical=(
            LaneReport(
                "wiki",
                Report((Finding("links.broken", "warn", "broken", "§5", "concepts/a.md", 7),)),
            ),
        ),
        semantic=(SemanticFinding("page-quality", "thin", "concepts/a", "gpt-5"),),
        open_proposals=ProposalBacklog(count=1, oldest=date(2026, 8, 1), malformed=2, ages={"7-30d": 1}),
    )

    async def fake_run_lint(*_args: object, **_kwargs: object) -> LintReport:
        return report

    monkeypatch.setattr(lint_module, "run_lint", fake_run_lint)

    result = runner.invoke(app, ["wiki", "lint", "--json", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "ok": True,
        "mechanical": [
            {
                "lane": "wiki",
                "findings": [
                    {
                        "code": "links.broken",
                        "severity": "warn",
                        "message": "broken",
                        "spec": "§5",
                        "path": "concepts/a.md",
                        "line": 7,
                    }
                ],
            }
        ],
        "semantic": [{"group": "page-quality", "message": "thin", "page": "concepts/a", "model": "gpt-5"}],
        "open_proposals": {"count": 1, "oldest": "2026-08-01", "malformed": 2, "ages": {"7-30d": 1}},
        "errors": [],
    }


def test_lint_mechanical_errors_remain_machine_readable_but_fail(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Treating an error-severity mechanical finding as advisory would weaken the gate."""
    report = LintReport(
        mechanical=(LaneReport("wiki", Report((Finding("links.broken", "error", "broken", "§5", "concepts/a.md"),))),)
    )

    async def fake_run_lint(*_args: object, **_kwargs: object) -> LintReport:
        return report

    monkeypatch.setattr(lint_module, "run_lint", fake_run_lint)

    result = runner.invoke(app, ["wiki", "lint", "--json", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert json.loads(result.stdout)["ok"] is False
    assert "lint failed" in result.stderr


def test_lint_runtime_errors_exit_cleanly(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """An operational lint failure must not expose a traceback as command output."""

    async def fake_run_lint(*_args: object, **_kwargs: object) -> LintReport:
        raise ValueError("lint backend unavailable")

    monkeypatch.setattr(lint_module, "run_lint", fake_run_lint)

    result = runner.invoke(app, ["wiki", "lint", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "lint backend unavailable" in result.stderr


def test_lint_semantic_findings_are_advisory(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    """A semantic verdict must not convert an otherwise sound lint run into a failure."""

    async def fake_run_lint(*_args: object, **_kwargs: object) -> LintReport:
        return LintReport(semantic=(SemanticFinding("page-quality", "thin", None, "gpt-5"),))

    monkeypatch.setattr(lint_module, "run_lint", fake_run_lint)

    result = runner.invoke(app, ["wiki", "lint", "--workspace", str(initialized_workspace)])

    assert result.exit_code == 0
    assert "## Semantic" in result.stdout


@pytest.mark.parametrize("flag", ("--stale-days", "--log-gap-days", "--check"))
def test_lint_rejects_removed_flags(flag: str) -> None:
    """Donor threshold controls must not silently become inert CLI surface."""
    result = runner.invoke(app, ["wiki", "lint", flag, "1"])

    assert result.exit_code == 2
    assert f"No such option: {flag}" in result.stderr


def test_lint_reports_unreadable_configuration_before_running(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    """Lint reads configuration first; a config fault must not be reported as a lint finding."""

    def fail(
        _bundle_root: object,
        *,
        config_path: object = None,
        graph_dir: object = None,
        declarations_dir: object = None,
    ) -> object:
        raise ConfigError("config.yaml is malformed")

    monkeypatch.setattr(lint_module, "load_config", fail)

    result = runner.invoke(app, ["wiki", "lint", "--workspace", str(initialized_workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert "Error: config.yaml is malformed" in result.stderr

"""`gw agent-config show` routes and renders the core read-only report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clean agent-home root, so user-machine state cannot affect the report."""
    value = tmp_path / "home"
    value.mkdir()
    monkeypatch.setenv("HOME", str(value))
    monkeypatch.setenv("USERPROFILE", str(value))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "PI_CODING_AGENT_DIR"):
        monkeypatch.delenv(name, raising=False)
    return value


def test_show_project_json_uses_the_wire_payload_and_injected_home(tmp_path: Path, home: Path, monkeypatch) -> None:
    """Removing project bypass or CLI home injection would break its projected Pi layer."""

    def fail_discovery(*args):
        raise AssertionError("workspace discovery must be bypassed")

    monkeypatch.setattr("graph_works_cli.agent_config_cli.main.resolve_workspace", fail_discovery)
    project = tmp_path / "project"
    (project / ".pi").mkdir(parents=True)
    (project / ".pi" / "settings.json").write_text('{"setting": 1}', encoding="utf-8", newline="\n")

    result = runner.invoke(app, ["agent-config", "show", "--project", str(project), "--agent", "pi", "--json"])

    assert result.exit_code == 0, result.output
    (entry,) = json.loads(result.stdout)["projects"]
    assert entry["path"] == str(project.resolve())
    (pi,) = entry["agents"]
    assert pi["agent"] == "pi"
    assert pi["home"] == str(home / ".pi" / "agent")
    assert pi["layers"][1]["data"] == {"setting": 1}


def test_show_project_accepts_repeated_agent_options(tmp_path: Path, home: Path) -> None:
    """Dropping repeatable filtering would report Pi in addition to the two requested agents."""
    result = runner.invoke(
        app,
        ["agent-config", "show", "--project", str(tmp_path), "--agent", "codex", "--agent", "claude", "--json"],
    )

    assert result.exit_code == 0, result.output
    agents = json.loads(result.stdout)["projects"][0]["agents"]
    assert [entry["agent"] for entry in agents] == ["codex", "claude"]


def test_show_workspace_reports_root_and_declared_repositories(tmp_path: Path, home: Path) -> None:
    """Replacing workspace routing with the project path would omit declared repositories."""
    root, repository = tmp_path / "workspace", tmp_path / "repository"
    root.mkdir()
    repository.mkdir()
    (root / "workspace.yaml").write_text(
        f'version: 1\nrepositories:\n  code:\n    path: "{repository}"\n', encoding="utf-8", newline="\n"
    )

    result = runner.invoke(app, ["agent-config", "show", "--workspace", str(root), "--json"])

    assert result.exit_code == 0, result.output
    paths = [entry["path"] for entry in json.loads(result.stdout)["projects"]]
    assert paths == [str(root.resolve()), str(repository.resolve())]


def test_show_human_render_includes_agents_and_trust(tmp_path: Path, home: Path) -> None:
    """Bypassing the human renderer would lose the agent/trust-readable report."""
    result = runner.invoke(app, ["agent-config", "show", "--project", str(tmp_path)])

    assert result.exit_code == 0, result.output
    for text in ("claude", "codex", "pi", "trust:", "user"):
        assert text in result.stdout


def test_show_malformed_workspace_uses_schema_mismatch_exit(tmp_path: Path, home: Path) -> None:
    """Letting workspace errors escape would lose the CLI's schema-error contract."""
    (tmp_path / "workspace.yaml").write_text("repositories: [x]\n", encoding="utf-8", newline="\n")

    result = runner.invoke(app, ["agent-config", "show", "--workspace", str(tmp_path)])

    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert "Error:" in result.stderr


@pytest.mark.parametrize("container", ["object", "array"])
def test_deep_layer_is_excluded_and_wire_json_still_encodes(tmp_path, home, container):
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    nested = '{"a":' * 100 + "0" + "}" * 100 if container == "object" else "[" * 100 + "0" + "]" * 100
    (project / ".claude/settings.json").write_text('{"deep":' + nested + "}", encoding="utf-8", newline="\n")
    (home / ".claude").mkdir()
    (home / ".claude/settings.json").write_text('{"survives": true}', encoding="utf-8", newline="\n")
    result = runner.invoke(app, ["agent-config", "show", "--project", str(project), "--agent", "claude", "--json"])
    assert result.exit_code == 0, result.output
    agent = json.loads(result.stdout)["projects"][0]["agents"][0]
    assert agent["layers"][1]["parse"] == "error"
    assert agent["effective"] == {"survives": True}
    assert any(f["code"] == "agent-config.parse" for f in agent["findings"])


def test_windows_report_does_not_call_posix_user_lookup(tmp_path, home, monkeypatch):
    import graph_works_cli.agent_config_cli.main as main

    def forbidden_uid():
        raise AssertionError("POSIX user lookup is unavailable on Windows")

    monkeypatch.setattr(main.os, "getuid", forbidden_uid, raising=False)
    monkeypatch.setattr(main.sys, "platform", "win32")
    result = runner.invoke(app, ["agent-config", "show", "--project", str(tmp_path), "--agent", "claude", "--json"])
    assert result.exit_code == 0, result.output
    agent = json.loads(result.stdout)["projects"][0]["agents"][0]
    assert agent["layers"][2]["path"] == str(tmp_path / ".claude/settings.local.json")

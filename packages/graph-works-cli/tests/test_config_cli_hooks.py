"""CLI routing for graph_works_core.hooks; merge semantics stay in core tests."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path, PurePath

import typer
from graph_works_cli import exit_codes
from graph_works_cli.config_cli.main import config_app
from graph_works_core.hooks import HooksError, HooksResult
from typer.testing import CliRunner

runner = CliRunner()


def test_enable_routes_explicit_repo_and_renders_plain_result(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[tuple[str, str, Path]] = []

    def fake_apply(action: str, feature: str, repo_root: Path) -> HooksResult:
        calls.append((action, feature, repo_root))
        return HooksResult(
            settings_path=repo_root / ".claude" / "settings.local.json",
            changed=True,
            # Synthetic: this test's subject is the renderer's two branches,
            # which need two distinct names, and `transcript` -- the only
            # feature -- registers one script.
            added=("session-end-transcript-capture.sh",),
            skipped=("some-other-hook.sh",),
        )

    monkeypatch.setattr("graph_works_cli.config_cli.main.apply_hooks", fake_apply)

    result = runner.invoke(config_app, ["hooks", "enable", "transcript", "--repo", str(repo)])

    assert result.exit_code == 0
    assert calls == [("enable", "transcript", repo.resolve())]
    assert result.stdout.startswith(f"[ok] {repo / '.claude' / 'settings.local.json'}\n")
    assert "  added: session-end-transcript-capture.sh" in result.stdout
    assert "  already registered: some-other-hook.sh" in result.stdout


def test_disable_uses_git_walk_up_and_renders_json(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    calls: list[tuple[str, str, Path]] = []

    def fake_apply(action: str, feature: str, repo_root: Path) -> HooksResult:
        calls.append((action, feature, repo_root))
        return HooksResult(
            settings_path=repo_root / ".claude" / "settings.local.json",
            changed=True,
            removed=("session-end-transcript-capture.sh",),
        )

    monkeypatch.chdir(nested)
    monkeypatch.setattr("graph_works_cli.config_cli.main.apply_hooks", fake_apply)

    result = runner.invoke(config_app, ["hooks", "disable", "transcript", "--json"])

    assert result.exit_code == 0
    assert calls == [("disable", "transcript", repo.resolve())]
    payload = json.loads(result.stdout)
    assert payload == {
        "settings_path": str(repo / ".claude" / "settings.local.json"),
        "changed": True,
        "added": [],
        "removed": ["session-end-transcript-capture.sh"],
        "skipped": [],
    }


def test_repo_falls_back_to_cwd_outside_git(tmp_path: Path, monkeypatch) -> None:
    calls: list[Path] = []

    def fake_apply(action: str, feature: str, repo_root: Path) -> HooksResult:
        calls.append(repo_root)
        return HooksResult(settings_path=repo_root / ".claude" / "settings.local.json", changed=False)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graph_works_cli.config_cli.main.apply_hooks", fake_apply)

    result = runner.invoke(config_app, ["hooks", "disable", "transcript"])

    assert result.exit_code == 0
    assert calls == [tmp_path.resolve()]


def test_enable_transcript_uses_the_real_default_script(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = runner.invoke(config_app, ["hooks", "enable", "transcript", "--repo", str(repo)])

    assert result.exit_code == 0
    settings_path = repo / ".claude" / "settings.local.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    command = settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
    if sys.platform == "win32":
        tokens = [t.strip('"') for t in shlex.split(command, posix=False)]
    else:
        tokens = shlex.split(command)
    assert tokens[0] == sys.executable
    # The command is host-quoted argv, so this recovers the script path
    # verbatim on every host; compare it as a path, not as a byte suffix,
    # because str(Path) is backslash-separated on Windows and the property
    # under test is path identity.
    script = PurePath(tokens[-1])
    assert script.parts[-5:] == (
        "plugins",
        "gw",
        "hooks",
        "examples",
        "session-end-transcript-capture.py",
    )


def test_malformed_settings_prints_error_and_uses_schema_mismatch_exit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    settings_path = repo / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{", encoding="utf-8")

    result = runner.invoke(config_app, ["hooks", "disable", "transcript", "--repo", str(repo)])

    assert result.exit_code == exit_codes.SCHEMA_MISMATCH
    assert result.stdout == ""
    assert result.stderr.startswith(f"Error: {settings_path}: is not valid JSON:")


def test_hooks_error_prints_to_stderr_and_uses_generic_exit(tmp_path: Path, monkeypatch) -> None:
    def fake_apply(action: str, feature: str, repo_root: Path) -> HooksResult:
        raise HooksError("hook script missing: example.sh")

    monkeypatch.setattr("graph_works_cli.config_cli.main.apply_hooks", fake_apply)

    result = runner.invoke(config_app, ["hooks", "enable", "transcript", "--repo", str(tmp_path)])

    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == ""
    assert "Error: hook script missing: example.sh" in result.stderr


def test_invalid_hook_feature_is_rejected_by_typer() -> None:
    result = runner.invoke(config_app, ["hooks", "enable", "unknown"])

    assert result.exit_code == 2
    assert "transcript" in result.output


def test_config_surface_is_exact_and_does_not_restore_init() -> None:
    command = typer.main.get_command(config_app)

    assert set(command.commands) == {"get", "list", "set", "unset", "sync", "hooks"}
    assert set(command.commands["hooks"].commands) == {"enable", "disable"}

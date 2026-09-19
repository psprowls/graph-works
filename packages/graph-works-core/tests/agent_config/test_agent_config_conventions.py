"""Adapter conventions resolved against injected machine inputs."""

from __future__ import annotations

from pathlib import Path

from graph_works_core.agent_config.conventions import resolve_conventions


def test_claude_resolves_user_project_local_and_platform_managed(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "proj"
    resolved = resolve_conventions("claude", project=project, home=home, env={}, platform="linux")
    assert resolved.home == home / ".claude"
    assert [(layer.scope, layer.path) for layer in resolved.layers] == [
        ("user", home / ".claude" / "settings.json"),
        ("project", project / ".claude" / "settings.json"),
        ("local", project / ".claude" / "settings.local.json"),
        ("managed", Path("/etc/claude-code/managed-settings.json")),
    ]
    assert resolved.trust_path == home / ".claude.json"


def test_home_env_relocates_the_agent_home_but_not_a_home_based_trust_file(tmp_path: Path) -> None:
    home, project, moved = tmp_path / "home", tmp_path / "proj", tmp_path / "elsewhere"
    claude = resolve_conventions(
        "claude", project=project, home=home, env={"CLAUDE_CONFIG_DIR": str(moved)}, platform="darwin"
    )
    assert claude.home == moved
    assert claude.layers[0].path == moved / "settings.json"
    assert claude.trust_path == home / ".claude.json"

    codex = resolve_conventions("codex", project=project, home=home, env={"CODEX_HOME": str(moved)}, platform="linux")
    assert codex.home == moved and codex.trust_path == moved / "config.toml"

    pi = resolve_conventions(
        "pi", project=project, home=home, env={"PI_CODING_AGENT_DIR": str(moved)}, platform="linux"
    )
    assert pi.home == moved and pi.trust_path == moved / "trust.json"


def test_empty_home_env_is_ignored(tmp_path: Path) -> None:
    resolved = resolve_conventions(
        "pi", project=tmp_path, home=tmp_path / "h", env={"PI_CODING_AGENT_DIR": ""}, platform="linux"
    )
    assert resolved.home == tmp_path / "h" / ".pi" / "agent"


def test_a_platform_with_no_managed_path_omits_the_managed_layer(tmp_path: Path) -> None:
    resolved = resolve_conventions("claude", project=tmp_path, home=tmp_path, env={}, platform="cygwin")
    assert [layer.scope for layer in resolved.layers] == ["user", "project", "local"]


def test_win32_managed_path(tmp_path: Path) -> None:
    resolved = resolve_conventions("claude", project=tmp_path, home=tmp_path, env={}, platform="win32")
    assert str(resolved.layers[-1].path).endswith("managed-settings.json")
    assert "Program Files" in str(resolved.layers[-1].path)


def test_relative_and_tilde_inputs_are_normalized_against_injected_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    resolved = resolve_conventions(
        "codex",
        project=Path("project"),
        home=Path("home"),
        env={"CODEX_HOME": "~/.configured"},
        platform="linux",
    )
    assert resolved.home == tmp_path / "home" / ".configured"
    assert resolved.layers[1].path == tmp_path / "project" / ".codex" / "config.toml"
    assert resolved.trust_path == tmp_path / "home" / ".configured" / "config.toml"
    assert all(layer.path.is_absolute() for layer in resolved.layers)

from plugin_fork_io.adapters import adapter


def test_documented_agent_defaults(tmp_path):
    project, home = tmp_path / "project", tmp_path / "home"
    assert adapter("codex").root("project", project=project, home=home) == project / ".agents/skills"
    assert adapter("claude").root("personal", project=project, home=home) == home / ".claude/skills"
    assert adapter("pi").root("personal", project=project, home=home) == home / ".pi/agent/skills"


def test_reserved_name_and_normalized_collision_key():
    from plugin_fork_io.adapters import normalized

    assert adapter("claude").validate_name("synced")
    assert not adapter("codex").validate_name("synced")
    assert normalized("E\u0301") == normalized("É")
    assert adapter("pi").invocation("review") == "/skill:review"


def test_missing_and_inaccessible_discovery_are_explicit(tmp_path):
    from dataclasses import replace

    from plugin_fork_io import Services
    from plugin_fork_io.adapters import discovered_names
    from plugin_fork_io.machine import LocalFileSystem

    names, findings = discovered_names(tmp_path / "missing", services=Services.local())
    assert not names and findings[0].code == "discovery.missing"

    class DeniedFS(LocalFileSystem):
        def children(self, path):
            raise PermissionError("unavailable")

    names, findings = discovered_names(tmp_path, services=replace(Services.local(), filesystem=DeniedFS()))
    assert not names and findings[0].code == "discovery.inaccessible"


def test_agent_config_conventions_are_pinned():
    """Verified 2026-09-18 against the sources cited in the agent-config-reads plan."""
    from plugin_fork_io.adapters import ConfigLayerSpec, TrustRecordSpec

    claude = adapter("claude").config
    assert (claude.home, claude.home_env) == (".claude", "CLAUDE_CONFIG_DIR")
    assert claude.layers == (
        ConfigLayerSpec("user", "agent_home", "settings.json", "json"),
        ConfigLayerSpec("project", "project", ".claude/settings.json", "json"),
        ConfigLayerSpec("local", "project", ".claude/settings.local.json", "json"),
        ConfigLayerSpec(
            "managed",
            "system",
            (
                ("darwin", "/Library/Application Support/ClaudeCode/managed-settings.json"),
                ("linux", "/etc/claude-code/managed-settings.json"),
                ("win32", "C:\\Program Files\\ClaudeCode\\managed-settings.json"),
            ),
            "json",
        ),
    )
    # ~/.claude.json sits beside the agent home and does not follow CLAUDE_CONFIG_DIR.
    assert claude.trust == TrustRecordSpec("home", ".claude.json", "claude-projects", True)

    codex = adapter("codex").config
    assert (codex.home, codex.home_env) == (".codex", "CODEX_HOME")
    assert codex.layers == (
        ConfigLayerSpec("user", "agent_home", "config.toml", "toml"),
        ConfigLayerSpec("project", "project", ".codex/config.toml", "toml"),
    )
    assert codex.trust == TrustRecordSpec("agent_home", "config.toml", "codex-projects", False)

    pi = adapter("pi").config
    assert (pi.home, pi.home_env) == (".pi/agent", "PI_CODING_AGENT_DIR")
    assert pi.layers == (
        ConfigLayerSpec("user", "agent_home", "settings.json", "json"),
        ConfigLayerSpec("project", "project", ".pi/settings.json", "json"),
    )
    assert pi.trust == TrustRecordSpec("agent_home", "trust.json", "pi-map", True)


def test_config_field_is_additive_and_defaulted():
    """Positional construction without the new field still works, which is what makes 0.1.1 a patch."""
    from plugin_fork_io.adapters import NO_CONFIG_CONVENTIONS, AgentAdapter

    bare = AgentAdapter("x", ".x/skills", ".x/skills", "/")
    assert bare.config is NO_CONFIG_CONVENTIONS
    assert bare.config.layers == () and bare.config.trust is None
    assert hash(adapter("claude").config)  # frozen and hashable: no Mapping inside

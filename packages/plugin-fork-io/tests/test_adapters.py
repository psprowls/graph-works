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

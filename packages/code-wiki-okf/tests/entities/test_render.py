import pytest
from code_graph_io import (
    AgentPluginDescription,
    AppDescription,
    DependencyDescription,
    EntryPointDescription,
    PackageDescription,
    SuiteDescription,
)
from code_wiki_okf.entities.render import (
    render_agent_plugin,
    render_app,
    render_dependency,
    render_package,
    render_repository,
    render_test_suite,
)


def test_render_package_owned_frontmatter_and_files_section() -> None:
    desc = PackageDescription(
        name="okf-io",
        language="python",
        version="0.1.1",
        files=[
            "packages/okf-io/src/okf_io/document.py",
            "packages/okf-io/src/okf_io/bundle.py",
        ],
        counts={},
        entry_points=[
            EntryPointDescription(
                name="okf-io",
                uri="",
                kind="cli",
                callable=None,
                implemented_by_path=None,
                source="pyproject",
            )
        ],
        test_suites=[SuiteDescription(name="okf-io-tests", uri="", kind="pytest", file_count=12)],
        internal_dependencies=["code-graph-io"],
        internal_dependents=["code-wiki-okf"],
    )
    render = render_package(desc, repo_name="agent-workspace")
    assert render.frontmatter == {
        "language": "python",
        "version": "0.1.1",
        "depends_on": ["code-graph-io"],
        "test_suites": ["okf-io-tests"],
        "entry_points": ["okf-io"],
    }
    assert render.sections.keys() == {"Files"}
    assert "/repositories/agent-workspace/files/packages/okf-io/src/okf_io/bundle.py.md" in render.sections["Files"]
    assert "/repositories/agent-workspace/files/packages/okf-io/src/okf_io/document.py.md" in render.sections["Files"]


def test_render_package_empty_files_renders_none_placeholder() -> None:
    desc = PackageDescription(name="empty", language="python", version="0.0.0", files=[], counts={})
    render = render_package(desc, repo_name="agent-workspace")
    assert render.sections["Files"].strip() == "_(none)_"


def test_render_app_frontmatter_drops_packaging_facts_for_a_package_reference() -> None:
    desc = AppDescription(
        name="cli-app",
        language="typescript",
        version="1.0.0",
        app_kind="cli",
        app_signals=["has-shebang"],
        files=["apps/cli/src/index.ts"],
        counts={},
        entry_points=[],
        test_suites=[],
    )
    render = render_app(desc, repo_name="agent-workspace")
    assert render.frontmatter == {"package": "[cli-app](/repositories/agent-workspace/packages/cli-app.md)"}
    assert "/repositories/agent-workspace/files/apps/cli/src/index.ts.md" in render.sections["Files"]


def test_render_test_suite_uses_passed_in_tested_packages() -> None:
    desc = SuiteDescription(name="okf-io-tests", uri="test_suite:x/y/okf-io-tests", kind="pytest", file_count=12)
    render = render_test_suite(desc, tested_packages=["okf-io", "okf-ext"], repo_name="agent-workspace")
    assert render.frontmatter == {
        "tested_packages": ["okf-io", "okf-ext"],
        "suite_kind": "pytest",
        "file_count": 12,
    }
    assert render.sections.keys() == {"Files"}


def test_render_test_suite_lists_files_like_package_and_app() -> None:
    desc = SuiteDescription(
        name="okf-io-tests",
        uri="test_suite:x/y/okf-io-tests",
        kind="pytest",
        file_count=1,
        files=["packages/okf-io/tests/test_document.py"],
    )
    render = render_test_suite(desc, tested_packages=["okf-io"], repo_name="agent-workspace")
    assert "/repositories/agent-workspace/files/packages/okf-io/tests/test_document.py.md" in render.sections["Files"]


def test_render_test_suite_empty_files_renders_none_placeholder() -> None:
    desc = SuiteDescription(name="okf-io-tests", uri="test_suite:x/y/okf-io-tests", kind="pytest", file_count=0)
    render = render_test_suite(desc, tested_packages=["okf-io"], repo_name="agent-workspace")
    assert render.sections["Files"].strip() == "_(none)_"


@pytest.mark.parametrize(
    "implemented_by",
    [
        [],
        ["pkg:acme/one/ruamel.yaml"],
        ["pkg:acme/one/ruamel.yaml", "pkg:acme/two/ruamel.yaml"],
    ],
)
def test_render_dependency_preserves_zero_one_or_many_implementations(implemented_by: list[str]) -> None:
    desc = DependencyDescription(
        ecosystem="pypi",
        name="ruamel.yaml",
        uri="dependency:pypi/ruamel.yaml",
        versions_in_use=["0.18.6"],
        used_by=["okf-io", "okf-ext"],
        implemented_by=implemented_by,
    )
    render = render_dependency(desc)
    assert render.frontmatter == {
        "ecosystem": "pypi",
        "implemented_by": implemented_by,
        "used_by": ["okf-io", "okf-ext"],
        "versions_in_use": ["0.18.6"],
    }
    assert render.sections == {}


def test_render_agent_plugin_formats_all_six_component_lists() -> None:
    desc = AgentPluginDescription(
        name="graph-wiki",
        uri="agent_plugin:x/y/graph-wiki",
        ecosystem="claude-code",
        version="1.0.0",
        description="workflow tooling",
        commands=[{"id": "command:1", "name": "next", "description": "advance a work item"}],
        agents=[{"id": "agent:1", "name": "ingestor", "description": "", "model": "", "tools": []}],
        skills=[{"id": "skill:1", "name": "brainstorming", "description": "design before code"}],
        scripts=[{"id": "script:1", "path": "scripts/audit_delta.py", "lang": "python"}],
        hooks=[{"id": "hook:1", "event": "PreToolUse", "matchers": ["Skill"]}],
        mcp_servers=[{"id": "mcp:1", "name": "context7", "command": "npx"}],
    )
    render = render_agent_plugin(desc, repo_name="agent-workspace")
    assert render.frontmatter == {"ecosystem": "claude-code", "version": "1.0.0"}
    assert render.sections.keys() == {"Commands", "Agents", "Skills", "Scripts", "Hooks", "MCP servers"}
    assert "next" in render.sections["Commands"]
    assert "advance a work item" in render.sections["Commands"]
    assert "scripts/audit_delta.py" in render.sections["Scripts"]
    assert "(python)" in render.sections["Scripts"]
    assert "PreToolUse" in render.sections["Hooks"]
    assert "Skill" in render.sections["Hooks"]
    assert "context7" in render.sections["MCP servers"]
    assert "npx" in render.sections["MCP servers"]


def test_render_agent_plugin_empty_component_lists_render_none() -> None:
    desc = AgentPluginDescription(
        name="empty-plugin",
        uri="agent_plugin:x/y/empty-plugin",
        ecosystem="claude-code",
        version="1.0.0",
        description="",
    )
    render = render_agent_plugin(desc, repo_name="agent-workspace")
    for heading in ("Commands", "Agents", "Skills", "Scripts", "Hooks", "MCP servers"):
        assert render.sections[heading].strip() == "_(none)_"


def test_render_agent_plugin_without_sibling_package_omits_package_key() -> None:
    """A plain plugin root (no manifest, no facet_of edge) has
    `package_name=None` — `render_agent_plugin` must not invent a `package`
    frontmatter key for it."""
    desc = AgentPluginDescription(
        name="standalone-plugin",
        uri="agent_plugin:x/y/standalone-plugin",
        ecosystem="claude-code",
        version="1.0.0",
        description="",
    )
    render = render_agent_plugin(desc, repo_name="agent-workspace")
    assert render.frontmatter == {"ecosystem": "claude-code", "version": "1.0.0"}
    assert "package" not in render.frontmatter


def test_render_agent_plugin_with_sibling_package_adds_package_reference() -> None:
    """A plugin root that also carries a package manifest (facet model:
    `Package --facet_of--> agent_plugin`) gets the same `package` reference
    key as `render_app`, in the same markdown-link format."""
    desc = AgentPluginDescription(
        name="graph-works",
        uri="agent_plugin:x/y/graph-works",
        ecosystem="claude-code",
        version="1.0.0",
        description="",
        package_name="graph-works",
    )
    render = render_agent_plugin(desc, repo_name="agent-workspace")
    assert render.frontmatter == {
        "ecosystem": "claude-code",
        "version": "1.0.0",
        "package": "[graph-works](/repositories/agent-workspace/packages/graph-works.md)",
    }


def test_render_repository_only_owns_package_count() -> None:
    render = render_repository(package_count=7)
    assert render.frontmatter == {"package_count": 7}
    assert render.sections == {}


def test_render_agent_plugin_component_optional_fields_fallback() -> None:
    """Exercise the fallback branches when optional fields are missing or empty."""
    desc = AgentPluginDescription(
        name="minimal-plugin",
        uri="agent_plugin:x/y/minimal",
        ecosystem="claude-code",
        version="1.0.0",
        description="",
        commands=[{"id": "cmd:1", "name": "run"}],  # no description
        agents=[{"id": "agent:1", "name": "worker"}],  # no description
        skills=[{"id": "skill:1", "name": "think"}],  # no description
        scripts=[{"id": "script:1", "path": "main.py"}],  # no lang
        hooks=[{"id": "hook:1", "event": "PostExecute"}],  # no matchers
        mcp_servers=[{"id": "mcp:1", "name": "local"}],  # no command
    )
    render = render_agent_plugin(desc, repo_name="agent-workspace")

    # Test _named_line fallback: command with no description
    assert "- **run**\n" in render.sections["Commands"]
    assert ": " not in render.sections["Commands"]  # no trailing ": ..."

    # Test _named_line fallback: agent with no description
    assert "- **worker**\n" in render.sections["Agents"]

    # Test _named_line fallback: skill with no description
    assert "- **think**\n" in render.sections["Skills"]

    # Test _script_line fallback: script with no lang
    assert "- `main.py`\n" in render.sections["Scripts"]
    assert "()" not in render.sections["Scripts"]  # no lang suffix

    # Test _hook_line fallback: hook with no matchers
    assert "- **PostExecute**\n" in render.sections["Hooks"]
    assert ": " not in render.sections["Hooks"]  # no trailing ": ..."

    # Test _mcp_server_line fallback: mcp_server with no command
    assert "- **local**\n" in render.sections["MCP servers"]
    assert ": `" not in render.sections["MCP servers"]  # no trailing ": `...`"

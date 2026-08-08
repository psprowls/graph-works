"""Pure `describe_* -> okf_ext.generators.Render` translations.

No I/O, no graph reads, no clock — matching `provenance.py`'s style.
Provenance keys (`generated`, `last_updated_commit`, `tokens`) are added by
`sync.py`, not here: a renderer knows a `PackageDescription`, not a commit SHA.

Frontmatter arrays are plain graph names, verbatim from the describe record —
not resolved to bundle links. `depends_on` is `internal_dependencies`
(outgoing) only; `internal_dependents` is never surfaced (backlinks stay
out of scope).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from code_graph_io import (
    AgentPluginDescription,
    AppDescription,
    DependencyDescription,
    PackageDescription,
    SuiteDescription,
)
from okf_ext.generators import Render

_NONE_PLACEHOLDER = "_(none)_"


def _files_section(files: Sequence[str], *, repo_name: str) -> str:
    """A `## Files` body: one root-absolute link per file into the mirror
    lane (child 3), keyed by the same `repositories/<repo>/<path>.md`
    convention that lane's E-F filename rule (verbatim plus `.md`) fixes.
    The link is written whether or not the mirror page exists yet — root-
    absolute links are never existence-checked (epic, "Links are root-
    absolute markdown links").
    """
    if not files:
        return _NONE_PLACEHOLDER
    lines = [f"- [{path}](/repositories/{repo_name}/{path}.md)" for path in sorted(files)]
    return "\n".join(lines) + "\n"


def render_package(desc: PackageDescription, *, repo_name: str) -> Render:
    return Render(
        frontmatter={
            "language": desc.language,
            "version": desc.version,
            "depends_on": list(desc.internal_dependencies),
            "test_suites": [suite.name for suite in desc.test_suites],
            "entry_points": [ep.name for ep in desc.entry_points],
        },
        sections={"Files": _files_section(desc.files, repo_name=repo_name)},
    )


def render_app(desc: AppDescription, *, repo_name: str) -> Render:
    return Render(
        frontmatter={
            "language": desc.language,
            "version": desc.version,
            "depends_on": [],  # AppDescription carries no internal_dependencies field
            "test_suites": [suite.name for suite in desc.test_suites],
            "entry_points": [ep.name for ep in desc.entry_points],
        },
        sections={"Files": _files_section(desc.files, repo_name=repo_name)},
    )


def render_test_suite(desc: SuiteDescription, *, tested_packages: Sequence[str]) -> Render:
    return Render(
        frontmatter={
            "tested_packages": list(tested_packages),
            "suite_kind": desc.kind,
            "file_count": desc.file_count,
        },
        sections={"Files": _NONE_PLACEHOLDER},
    )


def render_dependency(desc: DependencyDescription) -> Render:
    return Render(
        frontmatter={
            "ecosystem": desc.ecosystem,
            "used_by": list(desc.used_by),
            "versions_in_use": list(desc.versions_in_use),
        },
    )


def _component_lines(components: Sequence[dict[str, Any]], render_one: Callable[[dict[str, Any]], str]) -> str:
    if not components:
        return _NONE_PLACEHOLDER
    return "\n".join(render_one(c) for c in components) + "\n"


def _named_line(c: dict[str, Any]) -> str:
    name = c.get("name", "")
    description = c.get("description", "")
    return f"- **{name}**: {description}" if description else f"- **{name}**"


def _script_line(c: dict[str, Any]) -> str:
    path = c.get("path", "")
    lang = c.get("lang", "")
    return f"- `{path}` ({lang})" if lang else f"- `{path}`"


def _hook_line(c: dict[str, Any]) -> str:
    event = c.get("event", "")
    matchers = c.get("matchers") or []
    return f"- **{event}**: {', '.join(matchers)}" if matchers else f"- **{event}**"


def _mcp_server_line(c: dict[str, Any]) -> str:
    name = c.get("name", "")
    command = c.get("command", "")
    return f"- **{name}**: `{command}`" if command else f"- **{name}**"


def render_agent_plugin(desc: AgentPluginDescription) -> Render:
    return Render(
        frontmatter={"ecosystem": desc.ecosystem, "version": desc.version},
        sections={
            "Commands": _component_lines(desc.commands, _named_line),
            "Agents": _component_lines(desc.agents, _named_line),
            "Skills": _component_lines(desc.skills, _named_line),
            "Scripts": _component_lines(desc.scripts, _script_line),
            "Hooks": _component_lines(desc.hooks, _hook_line),
            "MCP servers": _component_lines(desc.mcp_servers, _mcp_server_line),
        },
    )


def render_repository(*, package_count: int) -> Render:
    return Render(frontmatter={"package_count": package_count})

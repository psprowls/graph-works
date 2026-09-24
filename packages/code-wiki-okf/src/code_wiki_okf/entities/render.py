"""Pure `describe_* -> okf_ext.generators.Render` translations.

No I/O, no graph reads, no clock — matching `provenance.py`'s style. The one
intra-package import is `placement`: the `## Files` links must agree with the
lane's writers about where a File page lives, and two hard-coded copies of
that path is how they stopped agreeing. Both sides now compute it from the
same policy rather than each spelling the lane out.
Provenance keys `generated` and `last_updated_commit` are added by `sync.py`,
not here: a renderer knows a `PackageDescription`, not a commit SHA. `tokens`
is also a declared provenance key but neither this module nor `sync.py` adds
it -- `run_tokens_update` (graph-works-core) is its sole writer.

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

from code_wiki_okf.placement import canonical_member, context_from_resource

_NONE_PLACEHOLDER = "_(none)_"


def _files_section(files: Sequence[str], *, repo_name: str) -> str:
    """A `## Files` body: one root-absolute link per file into the mirror
    lane, keyed by the canonical placement policy used by the lane's writers.

    **These links are existence-checked.** `okf_io._rules.links::broken` calls
    `ctx.bundle.has_member(...)` for every internal link, absolute form
    included, and reports `links.broken` at WARN
    (`adrs/0004-broken-links-are-warn`); there is no absolute-link exemption
    anywhere in the catalog. An earlier version of this docstring claimed the
    opposite and used it to justify emitting links into a lane the caller
    might never fill — which is exactly what `gw scan` did, for 1311
    warnings. The rule is correct as written and is not to be narrowed: any
    caller that renders these links must run the composite sync so the File
    targets are applied in the same preflighted operation.
    """
    if not files:
        return _NONE_PLACEHOLDER
    lines = [
        f"- [{path}](/{canonical_member(context_from_resource('File', f'file:placement/{repo_name}/{path}'))})"
        for path in sorted(files)
    ]
    return "\n".join(lines) + "\n"


def render_package(desc: PackageDescription, *, repo_name: str) -> Render:
    return Render(
        frontmatter={
            "language": desc.language,
            "version": desc.version,
            "depends_on": list(desc.internal_dependencies),
            "test_suites": [suite.name for suite in desc.test_suites],
            "entry_points": [ep.name for ep in desc.entry_points],
            "used_by": list(desc.used_by),
            "versions_in_use": list(desc.versions_in_use),
        },
        sections={"Files": _files_section(desc.files, repo_name=repo_name)},
    )


def _package_reference(name: str, *, repo_name: str) -> str:
    """Plain markdown link to a sibling Package page, given ITS OWN name —
    this vault's cross-reference convention (markdown links, not
    wikilinks). An App is always faceted off a Package of the same `name`,
    so `render_app` passes `desc.name` straight through. An agent_plugin's
    own `name` comes from `plugin.json`, independent of any co-located
    manifest's name, so `render_agent_plugin` must pass the sibling
    Package's OWN name (`desc.package_name`), not `desc.name`.

    *repo_name* is required because Package is a repo-scoped lane: the page
    lives at `code-graph/<repo>/entities/packages/<slug>.md`, matching
    `entities.pages.default_concept_id`. A facet always sits in the same
    repository as the Package it is faceted off, so the caller's own
    `repo_name` is the right one. Hardcoding the bundle-root `packages/`
    here would dangle on every App and dual-facet AgentPlugin page.
    """
    member = canonical_member(context_from_resource("Package", f"pkg:placement/{repo_name}/{name}"))
    return f"[{name}](/{member})"


def render_app(desc: AppDescription, *, repo_name: str) -> Render:
    return Render(
        frontmatter={"package": _package_reference(desc.name, repo_name=repo_name)},
        sections={"Files": _files_section(desc.files, repo_name=repo_name)},
    )


def render_test_suite(desc: SuiteDescription, *, tested_packages: Sequence[str], repo_name: str) -> Render:
    return Render(
        frontmatter={
            "tested_packages": list(tested_packages),
            "suite_kind": desc.kind,
            "file_count": desc.file_count,
        },
        sections={"Files": _files_section(desc.files, repo_name=repo_name)},
    )


def render_dependency(desc: DependencyDescription) -> Render:
    return Render(
        frontmatter={
            "ecosystem": desc.ecosystem,
            "implemented_by": list(desc.implemented_by),
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


def render_agent_plugin(desc: AgentPluginDescription, *, repo_name: str) -> Render:
    frontmatter: dict[str, Any] = {"ecosystem": desc.ecosystem, "version": desc.version}
    if desc.package_name is not None:
        frontmatter["package"] = _package_reference(desc.package_name, repo_name=repo_name)
    return Render(
        frontmatter=frontmatter,
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

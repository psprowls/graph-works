"""Fixtures the scan-vertical suites share: a real workspace on disk, a seeded
graph over it, and small builders for pages and tasks.

The workspace is built the way a caller builds one -- `plan_init` then
`apply_init` -- so the `sections/` declarations under test are the shipped
assets, not a hand-written copy that could drift from them.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

from code_graph_io.testing import raw_conn
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.layout import WorkspaceLayout
from ruamel.yaml import YAML

TODAY = date(2026, 8, 13)
AT = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

REPO_NAME = "demo"
REPO_URI = "repo:acme/demo"
PACKAGE_URI = "pkg:acme/demo/widgets"
#: A package whose *directory* name carries a dot. The F6 regression: nothing
#: about it is a manifest, and a `path.suffix` heuristic reads `.bar` as a file
#: extension and widens its diff scope to `packages/`.
DOTTED_PACKAGE_URI = "pkg:acme/demo/foobar"
APP_URI = "app:acme/demo/console"
SUITE_URI = "test_suite:acme/demo/tests"
PLUGIN_URI = "agent_plugin:acme/demo/demo-plugin"
DEPENDENCY_URI = "dependency:pypi/httpx"

#: Every lane node's `path` is the entity's **directory**, which is what
#: `code_graph_io` actually stores -- a package's is its manifest's *parent*
#: (`packages.py:327`), never the manifest. The previous fixture hand-seeded
#: `packages/widgets/pyproject.toml`, which made `_entity_relative_root`'s
#: suffix heuristic look correct by arriving at the right answer through the
#: wrong route. Dependency nodes are the one exception: the builder stores
#: `dependency:{ecosystem}:{name}` there, and `entity_refs` never reads it --
#: a dependency has no repo, no root and no head.
_NODES: tuple[tuple[int, str, str, str | None, dict[str, object], str], ...] = (
    (
        1,
        "repository",
        REPO_NAME,
        "",
        {"owner": "acme", "url": "https://example.com/acme/demo", "default_branch": "main", "uri": REPO_URI},
        REPO_URI,
    ),
    (
        2,
        "package",
        "widgets",
        "packages/widgets",
        {"language": "python", "version": "1.0.0", "uri": PACKAGE_URI},
        PACKAGE_URI,
    ),
    (3, "file", "a.py", "packages/widgets/src/a.py", {"language": "python"}, ""),
    (
        4,
        "package",
        "foobar",
        "packages/foo.bar",
        {"language": "python", "version": "0.1.0", "uri": DOTTED_PACKAGE_URI},
        DOTTED_PACKAGE_URI,
    ),
    (
        5,
        "app",
        "console",
        "apps/console",
        {"language": "python", "version": "2.0.0", "uri": APP_URI},
        APP_URI,
    ),
    (
        6,
        "test_suite",
        "tests",
        "tests",
        {"suite_kind": "unit", "file_count": 1, "uri": SUITE_URI},
        SUITE_URI,
    ),
    (
        7,
        "agent_plugin",
        "demo-plugin",
        "plugins/demo-plugin",
        {"ecosystem": "claude-code", "version": "0.1.0", "uri": PLUGIN_URI},
        PLUGIN_URI,
    ),
    (
        8,
        "dependency",
        "httpx",
        "dependency:pypi:httpx",
        {"ecosystem": "pypi", "versions_in_use": ["0.27.0"], "uri": DEPENDENCY_URI},
        DEPENDENCY_URI,
    ),
)

_EDGES: tuple[tuple[int, int, str], ...] = (
    (1, 2, "contains"),
    (2, 3, "contains"),
    (1, 4, "contains"),
    (1, 5, "contains"),
)


def git(repo: Path, *args: str) -> str:
    """Run one git command in *repo* and return its stdout, raising on failure."""
    done = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True)
    return done.stdout.strip()


def make_repo(root: Path) -> Path:
    """A real single-commit git repo carrying one file per seeded entity.

    Every entity the graph names gets a directory here, so `changed_files_since`
    has a real sub-path to scope a diff against. `packages/foo.bar` is the F6
    regression: a package *directory* whose name carries a dot.
    """
    repo = root / "repo"
    files = {
        "packages/widgets/pyproject.toml": "[project]\nname = 'widgets'\n",
        "packages/widgets/src/a.py": "def alpha():\n    return 1\n",
        "packages/foo.bar/pyproject.toml": "[project]\nname = 'foobar'\n",
        "packages/foo.bar/src/b.py": "def beta():\n    return 1\n",
        "apps/console/pyproject.toml": "[project]\nname = 'console'\n",
        "apps/console/src/main.py": "def main():\n    return 0\n",
        "tests/test_alpha.py": "def test_alpha():\n    assert True\n",
        "plugins/demo-plugin/plugin.json": '{"name": "demo-plugin"}\n',
        "README.md": "# demo\n",
    }
    for relative, text in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git(repo.parent, "init", repo.name)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "first")
    return repo


def commit_change(repo: Path, relative: str, text: str) -> str:
    """Write *text* to *relative*, commit it, return the new HEAD sha."""
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", f"edit {relative}")
    return git(repo, "rev-parse", "HEAD")


def seed_graph(graph_dir: Path, repo: Path) -> None:
    """Seed `code.db` with one node per lane type, plus one mirror file.

    These rows survive `commands.graph.build`: the build derives its own nodes
    under a different repo URI (`repo:local/repo`, from the checkout's own
    remote-less context), and `entity_refs` keys off `Config.repositories`'
    `demo` -> `repo:acme/demo`, so only these rows ever reach phase 1.
    """
    graph_dir.mkdir(parents=True, exist_ok=True)
    conn = raw_conn(graph_dir / "code.db", create=True)
    try:
        with conn:
            for node_id, kind, name, path, attrs, uri in _NODES:
                conn.execute(
                    "INSERT INTO nodes (id, kind, name, path, line, attrs_json, uri, repo) "
                    "VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
                    (node_id, kind, name, path, json.dumps({**attrs, "repo": REPO_URI}), uri or None, REPO_URI),
                )
            conn.executemany("INSERT INTO edges (src, dst, kind) VALUES (?, ?, ?)", _EDGES)
    finally:
        conn.close()
    _ = repo  # the seeded graph is independent of the checkout; kept for call-site symmetry


def make_workspace(tmp_path: Path) -> tuple[WorkspaceLayout, Path]:
    """A real workspace beside a real repo, with `workspace.yaml`'s `repositories`
    block pointing at it.

    Returns `(layout, repo)`. The graph is seeded but the caller decides whether
    to let `commands.graph.build` re-run over it.
    """
    repo = make_repo(tmp_path)
    init = apply_init(plan_init(tmp_path / ".works", today=TODAY, topic="Scan"))
    layout = init.layout
    # `repositories`/`ignore`/`state_gate` are merged into `workspace.yaml`
    # itself (ADR-0033), alongside `version`/`layout`/`roles` that `init`
    # already rendered -- so this loads the pristine document with the
    # YAML round-trip loader, edits just the three blocks `load_config`
    # reads, and rewrites the whole file. That keeps every other key
    # (`version`, `layout.*`, `roles`) intact instead of depending on this
    # test's own idea of the template's exact shape.
    yaml = YAML()
    yaml.preserve_quotes = True
    with layout.manifest_path.open(encoding="utf-8") as handle:
        data = yaml.load(handle)
    data["repositories"] = {REPO_NAME: {"path": str(repo)}}
    data["state_gate"] = {"enabled": False}
    with layout.manifest_path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)
    return layout, repo


def write_page(layout: WorkspaceLayout, relative: str, text: str) -> Path:
    path = layout.bundle_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _page(
    *,
    type_name: str,
    title: str,
    resource: str,
    sections: dict[str, str],
    last_updated_commit: str | None = None,
    prose_refreshed_commit: str | None = None,
    prose_refresh_attempts: int | None = None,
) -> str:
    """One entity page's full text, with its prose sections under control.

    SHAs are quoted: an unquoted all-digit scalar like `"0" * 40` parses under
    YAML 1.1's resolver as an octal integer (0), not the 40-char SHA the test
    means to write. A real git SHA never collides with this -- it always
    contains a hex digit outside 0-7 -- but the synthetic "unknown anchor"
    fixture value does.
    """
    lines = ["---", f"type: {type_name}", f'title: "{title}"', f'resource: "{resource}"', 'description: ""']
    if last_updated_commit is not None:
        lines.append(f'last_updated_commit: "{last_updated_commit}"')
    if prose_refreshed_commit is not None:
        lines.append(f'prose_refreshed_commit: "{prose_refreshed_commit}"')
    if prose_refresh_attempts is not None:
        lines.append(f"prose_refresh_attempts: {prose_refresh_attempts}")
    lines.append("---")
    body = "".join(f"\n## {heading}\n\n{text}\n" for heading, text in sections.items())
    return "\n".join(lines) + "\n" + body


#: Each declaration's prose placeholders, verbatim from
#: `code_wiki_okf/assets/sections/*.yaml`. A body equal to one of these is what
#: `is_unfilled` calls a first fill.
PLACEHOLDERS: dict[str, dict[str, str]] = {
    "Package": {
        "Purpose": "> TODO: what this package does, who uses it, and why it exists, in one paragraph.",
        "Public API": (
            "> TODO: the main exports and when to use them. Link code with backticked `path:line` references."
        ),
    },
    "App": {
        "Purpose": "> TODO: what this app does, who uses it, and why it exists, in one paragraph.",
        "Platform & runtime": "> TODO: the target platform(s) and runtime — e.g. web/Node, iOS, Electron.",
        "Routes / screens": "> TODO: a table of Route | Purpose | Auth, or the equivalent for this platform.",
        "Provider chain": "> TODO: the top-level providers/wrappers this app mounts, in order.",
    },
    "TestSuite": {
        "Purpose": "> TODO: what this suite tests and why, in one paragraph.",
        "How to run": "> TODO: the exact command(s) to run this suite.",
        "Test conventions": "> TODO: the naming, structure, and style conventions this suite follows.",
        "Fixtures": "> TODO: the shared fixtures this suite relies on, and where they live.",
    },
    "AgentPlugin": {
        "Purpose": "> TODO: what this agent plugin does and who uses it, in one paragraph.",
        "How it fits together": "> TODO: the inferred cross-component relationships.",
    },
    "Repository": {
        "Overview": "> TODO: what this repository is and what it contains, in one paragraph.",
        "Layout": "> TODO: the top-level directory layout, and what lives where.",
    },
    "Dependency": {
        "Why we depend on this": "> TODO: why the workspace depends on this, and what it is used for.",
        "Gotchas / workarounds": ("> TODO: known issues, version pins, or workarounds this dependency needs."),
    },
}

#: The generated sections each declaration carries, with the body `sync` seeds.
#: Present so a fixture page is a *complete* page: `is_unfilled` returns False
#: for a section that is not on the page at all, so a page missing its generated
#: sections would silently pass a refill gate it should not.
_GENERATED: dict[str, dict[str, str]] = {
    "Package": {"Files": "_(none)_"},
    "App": {"Files": "_(none)_"},
    "TestSuite": {"Files": "_(none)_"},
    "AgentPlugin": {
        "Commands": "_(none)_",
        "Agents": "_(none)_",
        "Skills": "_(none)_",
        "Scripts": "_(none)_",
        "Hooks": "_(none)_",
        "MCP servers": "_(none)_",
    },
    "Repository": {"Contents": "_(none)_"},
    "Dependency": {},
}


def entity_page(
    type_name: str,
    *,
    title: str,
    resource: str,
    bodies: dict[str, str] | None = None,
    last_updated_commit: str | None = None,
    prose_refreshed_commit: str | None = None,
    prose_refresh_attempts: int | None = None,
) -> str:
    """A page of *type_name* whose prose sections default to their placeholders.

    `bodies` overrides individual prose sections by heading; anything not named
    keeps its placeholder, which is what makes the page a `first_fill`
    candidate for exactly the sections the caller left alone.
    """
    sections = dict(PLACEHOLDERS[type_name])
    sections.update(bodies or {})
    sections.update(_GENERATED[type_name])
    return _page(
        type_name=type_name,
        title=title,
        resource=resource,
        sections=sections,
        last_updated_commit=last_updated_commit,
        prose_refreshed_commit=prose_refreshed_commit,
        prose_refresh_attempts=prose_refresh_attempts,
    )


def package_page(
    *,
    purpose: str = "> TODO: what this package does, who uses it, and why it exists, in one paragraph.",
    public_api: str = (
        "> TODO: the main exports and when to use them. Link code with backticked `path:line` references."
    ),
    last_updated_commit: str | None = None,
    prose_refreshed_commit: str | None = None,
    prose_refresh_attempts: int | None = None,
) -> str:
    """A `Package` page's full text, with the two prose sections under control."""
    return entity_page(
        "Package",
        title="widgets",
        resource=PACKAGE_URI,
        bodies={"Purpose": purpose, "Public API": public_api},
        last_updated_commit=last_updated_commit,
        prose_refreshed_commit=prose_refreshed_commit,
        prose_refresh_attempts=prose_refresh_attempts,
    )

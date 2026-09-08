"""Graph-works composes the invariant code-wiki placement policy end to end."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from code_graph_io import open_reader
from code_graph_io.testing import raw_conn
from code_wiki_okf.config import Config, load_config
from code_wiki_okf.placement import PlacementError, placement_rule
from graph_works_core import apply_init, plan_init
from graph_works_core.lint_drift.lint import run_mechanical
from graph_works_core.scan import commands as scan
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_io import RuleContext, build_link_graph, load_bundle
from ruamel.yaml import YAML
from scan_helpers import AT, TODAY, make_repo, seed_graph

_TRACKED_FILES = (
    "README.md",
    "apps/console/pyproject.toml",
    "apps/console/src/main.py",
    "packages/foo.bar/pyproject.toml",
    "packages/foo.bar/src/b.py",
    "packages/widgets/pyproject.toml",
    "packages/widgets/src/a.py",
    "plugins/demo-plugin/plugin.json",
    "tests/test_alpha.py",
)

#: ADR-0039 dropped the four global lane indexes (`{packages,apps,agent-plugins,
#: test-suites}/index.md`): the bundle root index already carries those exact
#: sections, built from the same flat cross-repo list by the same code. The
#: per-repository lanes below (`{prefix}/packages/index.md` and friends) are
#: untouched by that ADR and stay.
#:
#: `console`, `foobar` and `widgets` are workspace-implemented (their own
#: manifests self-implement their same-named Dependency node), so under
#: ADR-0034 as narrowed by ADR-0048 they get no `dependencies/` page at all —
#: `used_by`/`versions_in_use` migrate onto their Package pages instead. Only
#: `httpx` (genuinely external, in both ecosystems here) keeps a page.
_GLOBAL_MEMBERS = frozenset(
    {
        "index.md",
        "log.md",
        "dependencies/index.md",
        "dependencies/npm/httpx.md",
        "dependencies/npm/index.md",
        "dependencies/pypi/httpx.md",
        "dependencies/pypi/index.md",
        "repositories/index.md",
    }
)


def _repository_members(repository: str, *, complete_entities: bool) -> frozenset[str]:
    prefix = f"repositories/{repository}"
    members = {
        f"{prefix}/index.md",
        f"{prefix}/repository.md",
        f"{prefix}/agent-plugins/index.md",
        f"{prefix}/apps/index.md",
        f"{prefix}/files/index.md",
        f"{prefix}/packages/index.md",
        f"{prefix}/test-suites/index.md",
        f"{prefix}/files/apps/index.md",
        f"{prefix}/files/apps/console/index.md",
        f"{prefix}/files/apps/console/src/index.md",
        f"{prefix}/files/packages/index.md",
        f"{prefix}/files/packages/foo.bar/index.md",
        f"{prefix}/files/packages/foo.bar/src/index.md",
        f"{prefix}/files/packages/widgets/index.md",
        f"{prefix}/files/packages/widgets/src/index.md",
        f"{prefix}/files/plugins/index.md",
        f"{prefix}/files/plugins/demo-plugin/index.md",
        f"{prefix}/files/tests/index.md",
        *(f"{prefix}/files/{source}.md" for source in _TRACKED_FILES),
    }
    members.add(f"{prefix}/packages/widgets.md")
    if complete_entities:
        members.update(
            {
                f"{prefix}/agent-plugins/demo-plugin.md",
                f"{prefix}/apps/console.md",
                f"{prefix}/packages/foobar.md",
                f"{prefix}/test-suites/tests.md",
            }
        )
    return frozenset(members)


def expected_policy_members(repositories: tuple[str, ...]) -> frozenset[str]:
    """Literal spec paths; no production placement helper participates."""
    expected = set(_GLOBAL_MEMBERS)
    for repository in repositories:
        expected.update(_repository_members(repository, complete_entities=repository == "demo"))
    return frozenset(expected)


def member_set(bundle_root: Path) -> frozenset[str]:
    return frozenset(path.relative_to(bundle_root).as_posix() for path in bundle_root.rglob("*.md"))


def _bundle_bytes(bundle_root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(bundle_root).as_posix(): path.read_bytes()
        for path in sorted(bundle_root.rglob("*"))
        if path.is_file()
    }


def _configure(layout: WorkspaceLayout, repositories: dict[str, Path]) -> Config:
    yaml = YAML()
    yaml.preserve_quotes = True
    with layout.manifest_path.open(encoding="utf-8") as handle:
        manifest = yaml.load(handle)
    manifest["repositories"] = {name: {"path": str(path)} for name, path in repositories.items()}
    manifest["state_gate"] = {"enabled": False}
    with layout.manifest_path.open("w", encoding="utf-8") as handle:
        yaml.dump(manifest, handle)
    return load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )


def _seed_other_repository(graph_dir: Path) -> None:
    repo_uri = "repo:acme/other"
    conn = raw_conn(graph_dir / "code.db")
    try:
        with conn:
            conn.executemany(
                "INSERT INTO nodes (id, kind, name, path, line, attrs_json, uri, repo) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    (
                        101,
                        "repository",
                        "other",
                        "",
                        json.dumps(
                            {
                                "owner": "acme",
                                "url": "https://example.com/acme/other",
                                "default_branch": "main",
                                "uri": repo_uri,
                                "repo": repo_uri,
                            }
                        ),
                        repo_uri,
                        repo_uri,
                    ),
                    (
                        102,
                        "package",
                        "widgets",
                        "packages/widgets",
                        json.dumps(
                            {
                                "language": "python",
                                "version": "1.0.0",
                                "uri": "pkg:acme/other/widgets",
                                "repo": repo_uri,
                            }
                        ),
                        "pkg:acme/other/widgets",
                        repo_uri,
                    ),
                ),
            )
            conn.execute("INSERT INTO edges (src, dst, kind) VALUES (101, 102, 'contains')")
    finally:
        conn.close()


def _seed_npm_dependency(graph_dir: Path) -> None:
    conn = raw_conn(graph_dir / "code.db")
    try:
        with conn:
            conn.execute(
                "INSERT INTO nodes (id, kind, name, path, line, attrs_json, uri, repo) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    100,
                    "dependency",
                    "httpx",
                    "dependency:npm:httpx",
                    json.dumps(
                        {
                            "ecosystem": "npm",
                            "versions_in_use": ["1.0.0"],
                            "uri": "dependency:npm/httpx",
                            "repo": "repo:acme/demo",
                        }
                    ),
                    "dependency:npm/httpx",
                    "repo:acme/demo",
                ),
            )
    finally:
        conn.close()


def make_placement_workspace(
    tmp_path: Path, workspace_location: str
) -> tuple[WorkspaceLayout, Config, tuple[str, ...]]:
    first_root = tmp_path / "first"
    demo = make_repo(first_root)
    repositories = {"demo": demo}

    if workspace_location == "inside":
        workspace_root = demo / ".works"
    elif workspace_location == "beside":
        workspace_root = first_root / ".works"
    else:
        other = make_repo(tmp_path / "second")
        renamed = other.with_name("other-checkout")
        other.rename(renamed)
        other = renamed
        repositories["other"] = other
        workspace_root = tmp_path / "shared" / ".works"

    layout = apply_init(plan_init(workspace_root, today=TODAY, topic="Placement")).layout
    config = _configure(layout, repositories)
    seed_graph(config.graph_dir, demo)
    _seed_npm_dependency(config.graph_dir)
    if "other" in repositories:
        _seed_other_repository(config.graph_dir)
    return layout, config, tuple(repositories)


@pytest.mark.parametrize("workspace_location", ["inside", "beside", "outside-multiple"])
async def test_workspace_location_does_not_change_inner_bundle_layout(tmp_path, workspace_location) -> None:
    layout, config, repositories = make_placement_workspace(tmp_path, workspace_location)

    await scan.run_scan(layout, config, today=TODAY, at=AT, narrate=False, dry_run=False)

    assert member_set(layout.bundle_dir) == expected_policy_members(repositories)

    bundle = load_bundle(layout.bundle_dir)
    policy_findings = tuple(
        placement_rule(severity="error")(RuleContext(bundle=bundle, links=build_link_graph(bundle), today=TODAY))
    )
    assert policy_findings == ()

    with open_reader(graph_dir=config.graph_dir) as reader:
        strict = run_mechanical(
            layout,
            config,
            today=TODAY,
            repo_root=layout.repo_root,
            reader=reader,
            strict=True,
        )
    assert strict.errors == ()
    assert [lane.name for lane in strict.mechanical] == ["wiki", "work"]
    strict_codes = {finding.code for lane in strict.mechanical for finding in lane.report.findings}
    assert not any(code.startswith(("placement.", "render.", "schemas.", "tags.", "sync.")) for code in strict_codes)
    # A structural-only scan intentionally leaves prose placeholders for the
    # narration phase; strict validation promotes that content-quality warning,
    # proving the installed section rules actually ran without disguising it as
    # a placement failure.
    assert "sections.unfilled" in strict_codes

    before_second_scan = _bundle_bytes(layout.bundle_dir)
    second = await scan.run_scan(layout, config, today=TODAY, at=AT, narrate=False, dry_run=False)
    assert _bundle_bytes(layout.bundle_dir) == before_second_scan
    assert second.structural.entities.written == ()
    assert second.structural.mirror.created == 0
    assert second.structural.mirror.regenerated == 0
    assert second.structural.mirror.moved == 0
    assert second.structural.mirror.deleted == 0


async def test_misplaced_page_refuses_scan_before_any_partial_sync_write(tmp_path) -> None:
    layout, config, _repositories = make_placement_workspace(tmp_path, "beside")
    await scan.run_scan(layout, config, today=TODAY, at=AT, narrate=False, dry_run=False)
    canonical = layout.bundle_dir / "repositories" / "demo" / "packages" / "widgets.md"
    misplaced = layout.bundle_dir / "packages" / "widgets.md"
    misplaced.parent.mkdir(exist_ok=True)
    canonical.replace(misplaced)
    before = _bundle_bytes(layout.bundle_dir)

    with pytest.raises(PlacementError, match="repositories/demo/packages/widgets"):
        await scan.run_scan(layout, config, today=TODAY, at=AT, narrate=False, dry_run=False)

    assert _bundle_bytes(layout.bundle_dir) == before

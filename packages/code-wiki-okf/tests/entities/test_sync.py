"""Tests for the GraphReader-driven entity-lane orchestrator.

Fixture graphs are built directly through `code_graph_io.testing.open_store`
(a writable `GraphStore` on an arbitrary db path) rather than a nonexistent
`code_graph_io.testing.build_records` helper -- see this task's brief. Node
shapes below are read from the real emitters
(`code_graph_io/structural_nodes.py`, `test_suites.py`, `agent_plugins.py`)
and confirmed to round-trip through `code_graph_io.open_reader` before being
used here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from code_graph_io import open_reader
from code_graph_io.testing import open_store
from code_parser.projections.graph import GraphNode, GraphRecords
from code_wiki_okf.config import Config, RepoConfig, StateGateConfig
from code_wiki_okf.entities.sync import plan_entities, sync_entities
from code_wiki_okf.init import install_bundle
from okf_io import load_bundle

_TODAY = date(2026, 1, 1)
_AT = datetime(2026, 1, 1, tzinfo=UTC)


# --- Fixture node factories -------------------------------------------------
# Attrs shapes mirror the real emitters closely enough for the describe_*
# queries to resolve, kept minimal per this task's brief ("only enough to
# prove the acceptance criteria, not a realistic full graph").


def _repo_node(org: str, repo: str) -> GraphNode:
    return GraphNode(
        kind="repository",
        name=repo,
        path="",
        line=None,
        attrs={"uri": f"repo:{org}/{repo}", "owner": org, "name": repo, "url": "", "default_branch": "main"},
    )


def _package_node(org: str, repo: str, name: str, *, version: str = "0.1.0") -> GraphNode:
    return GraphNode(
        kind="package",
        name=name,
        path=f"packages/{name}/pyproject.toml",
        line=None,
        attrs={"uri": f"pkg:{org}/{repo}/{name}", "language": "python", "version": version},
    )


def _app_node(org: str, repo: str, name: str) -> GraphNode:
    return GraphNode(
        kind="app",
        name=name,
        path=f"apps/{name}/package.json",
        line=None,
        attrs={
            "uri": f"app:{org}/{repo}/{name}",
            "language": "typescript",
            "version": "1.0.0",
            "app_kind": "cli",
            "app_signals": [],
        },
    )


def _test_suite_node(org: str, repo: str, name: str = "tests") -> GraphNode:
    return GraphNode(
        kind="test_suite",
        name=name,
        path=name,
        line=None,
        attrs={
            "uri": f"test_suite:{org}/{repo}/{name}",
            "suite_kind": "unit",
            "path": name,
            "owner_kind": "repository",
        },
    )


def _agent_plugin_node(org: str, repo: str, name: str) -> GraphNode:
    return GraphNode(
        kind="agent_plugin",
        name=name,
        path=".claude-plugin",
        line=None,
        attrs={
            "uri": f"agent_plugin:{org}/{repo}/{name}",
            "ecosystem": "claude-code",
            "name": name,
            "version": "1.0.0",
            "description": "demo plugin",
            "components": {
                "commands": [],
                "agents": [],
                "skills": [],
                "scripts": [],
                "hooks": [],
                "mcp_servers": [],
            },
        },
    )


def _dependency_node(ecosystem: str, name: str, *, versions: Sequence[str] = ("2.31.0",)) -> GraphNode:
    return GraphNode(
        kind="dependency",
        name=name,
        path=f"dependency:{ecosystem}/{name}",
        line=None,
        attrs={"uri": f"dependency:{ecosystem}/{name}", "ecosystem": ecosystem, "versions_in_use": list(versions)},
    )


class _RepoSeed:
    def __init__(
        self,
        org: str,
        repo: str,
        *,
        packages: Sequence[str] = (),
        apps: Sequence[str] = (),
        test_suites: Sequence[str] = (),
        agent_plugins: Sequence[str] = (),
    ) -> None:
        self.org = org
        self.repo = repo
        self.packages = packages
        self.apps = apps
        self.test_suites = test_suites
        self.agent_plugins = agent_plugins


def _seed(graph_dir: Path, repos: Sequence[_RepoSeed], dependencies: Sequence[GraphNode] = ()) -> None:
    """Seed `graph_dir/code.db` (via the public `GraphStore` upsert surface,
    never raw SQL) with one Repository per `repos` entry plus its declared
    Package/App/TestSuite/AgentPlugin nodes, and any ecosystem-wide
    Dependency nodes (unattributed to a repo, matching production: a
    dependency is one of code-graph-io's `_GLOBAL_KINDS`)."""
    store = open_store(graph_dir / "code.db", create=True)
    try:
        for seed in repos:
            store.set_current_repo(f"repo:{seed.org}/{seed.repo}")
            nodes: list[GraphNode] = [_repo_node(seed.org, seed.repo)]
            nodes += [_package_node(seed.org, seed.repo, name) for name in seed.packages]
            nodes += [_app_node(seed.org, seed.repo, name) for name in seed.apps]
            nodes += [_test_suite_node(seed.org, seed.repo, name) for name in seed.test_suites]
            nodes += [_agent_plugin_node(seed.org, seed.repo, name) for name in seed.agent_plugins]
            with store.transaction() as tx:
                tx.upsert_records(GraphRecords(nodes=tuple(nodes), edges=()))
            store.set_current_repo(None)
        if dependencies:
            with store.transaction() as tx:
                tx.upsert_records(GraphRecords(nodes=tuple(dependencies), edges=()))
    finally:
        store.close()


def _bump_package_version(graph_dir: Path, *, org: str, repo: str, name: str, version: str) -> None:
    """Re-upsert a Package node with a new `version`, identity-matched
    (kind, name, path, repo) onto the existing row -- an update, not a
    duplicate."""
    store = open_store(graph_dir / "code.db", create=True)
    try:
        store.set_current_repo(f"repo:{org}/{repo}")
        with store.transaction() as tx:
            tx.upsert_records(GraphRecords(nodes=(_package_node(org, repo, name, version=version),), edges=()))
        store.set_current_repo(None)
    finally:
        store.close()


def _config(tmp_path: Path, graph_dir: Path, repo_names: Sequence[str], *, bundle_root: Path) -> Config:
    return Config(
        graph_dir=graph_dir,
        declarations_dir=bundle_root,
        repos=tuple(RepoConfig(name=name, path=tmp_path / name, ignore=()) for name in repo_names),
        state_gate=StateGateConfig(enabled=False, branches=()),
    )


# --- Sanity: the fixture pattern itself round-trips -------------------------


def test_fixture_seed_round_trips_through_open_reader(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "repo-a", packages=["widgets"])])
    with open_reader(graph_dir=graph_dir) as reader:
        assert [n.name for n in reader.list_repositories()] == ["repo-a"]
        assert [n.name for n in reader.list_packages()] == ["widgets"]
        desc = reader.describe_package(name="widgets")
        assert desc is not None
        assert desc.language == "python"


# --- Acceptance criteria -----------------------------------------------------


def test_sync_creates_one_page_per_kind_and_is_idempotent(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(
        graph_dir,
        [
            _RepoSeed(
                "acme",
                "repo-a",
                packages=["widgets"],
                apps=["cli-app"],
                test_suites=["tests"],
                agent_plugins=["demo-plugin"],
            )
        ],
        dependencies=[_dependency_node("pypi", "requests")],
    )
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, ["repo-a"], bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        result = sync_entities(bundle, config, reader, today=_TODAY, at=_AT)

        expected = {
            "packages/widgets",
            "apps/cli-app",
            "test-suites/tests",
            "agent-plugins/demo-plugin",
            "dependencies/requests",
            "repositories/repo-a",
        }
        assert expected <= set(result.written)

        # second run: idempotent -- nothing left to regenerate. Uses a
        # DISTINCT `at` from the first call (not `_AT` again) so this
        # actually exercises "does a merely-different generated.at timestamp
        # get treated as real content drift" -- two identical `at` values
        # would pass even if provenance weren't excluded from the diff.
        later = datetime(2026, 6, 1, tzinfo=UTC)
        bundle2 = load_bundle(bundle_root)
        result2 = sync_entities(bundle2, config, reader, today=_TODAY, at=later)
        assert result2.written == ()

    bundle_after = load_bundle(bundle_root)

    pkg_doc = bundle_after.concept("packages/widgets")
    assert pkg_doc is not None
    assert pkg_doc.fm_raw.get("language") == "python"
    assert pkg_doc.fm_raw.get("version") == "0.1.0"
    assert pkg_doc.fm_raw.get("depends_on") == []
    assert pkg_doc.fm_raw.get("test_suites") == []
    assert pkg_doc.fm_raw.get("entry_points") == []
    generated = pkg_doc.fm_raw.get("generated")
    assert generated is not None and generated.get("by") == "code-wiki-okf/0.2.0"
    assert isinstance(pkg_doc.fm_raw.get("tokens"), int)
    assert pkg_doc.fm_raw.get("tokens") >= 0

    app_doc = bundle_after.concept("apps/cli-app")
    assert app_doc is not None
    assert app_doc.fm_raw.get("language") == "typescript"
    assert app_doc.fm_raw.get("version") == "1.0.0"

    suite_doc = bundle_after.concept("test-suites/tests")
    assert suite_doc is not None
    assert suite_doc.fm_raw.get("suite_kind") == "unit"
    assert suite_doc.fm_raw.get("file_count") == 0
    assert suite_doc.fm_raw.get("tested_packages") == []

    plugin_doc = bundle_after.concept("agent-plugins/demo-plugin")
    assert plugin_doc is not None
    assert plugin_doc.fm_raw.get("ecosystem") == "claude-code"
    assert plugin_doc.fm_raw.get("version") == "1.0.0"

    dep_doc = bundle_after.concept("dependencies/requests")
    assert dep_doc is not None
    assert dep_doc.fm_raw.get("ecosystem") == "pypi"
    assert dep_doc.fm_raw.get("versions_in_use") == ["2.31.0"]

    repo_doc = bundle_after.concept("repositories/repo-a")
    assert repo_doc is not None
    assert repo_doc.fm_raw.get("package_count") == 1


def test_sync_preserves_hand_edited_prose_section(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "repo-a", packages=["widgets"])])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, ["repo-a"], bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        sync_entities(bundle, config, reader, today=_TODAY, at=_AT)

    page = bundle_root / "packages" / "widgets.md"
    original = page.read_text(encoding="utf-8")
    placeholder = "> TODO: <One paragraph: what this package does, who uses it, why it exists.>"
    assert placeholder in original
    edited = original.replace(placeholder, "Widgets, hand-built for the acme storefront.")
    page.write_text(edited, encoding="utf-8")

    with open_reader(graph_dir=graph_dir) as reader:
        bundle2 = load_bundle(bundle_root)
        result = sync_entities(bundle2, config, reader, today=_TODAY, at=_AT)

    final_text = page.read_text(encoding="utf-8")
    assert "Widgets, hand-built for the acme storefront." in final_text
    assert "packages/widgets" not in result.written


def test_sync_page_moved_within_lane_is_updated_in_place_not_duplicated(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "repo-a", packages=["widgets"])])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, ["repo-a"], bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        sync_entities(bundle, config, reader, today=_TODAY, at=_AT)

    original = bundle_root / "packages" / "widgets.md"
    assert original.exists()
    moved = bundle_root / "packages" / "moved-widgets.md"
    moved.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
    original.unlink()

    # Bump the package's version in the graph too, so this test also proves
    # the moved page gets UPDATED at its new location, not left stale.
    _bump_package_version(graph_dir, org="acme", repo="repo-a", name="widgets", version="0.2.0")

    with open_reader(graph_dir=graph_dir) as reader:
        bundle2 = load_bundle(bundle_root)
        result = sync_entities(bundle2, config, reader, today=_TODAY, at=_AT)

    assert not original.exists()
    assert moved.exists()
    assert "packages/moved-widgets" in result.written

    bundle_after = load_bundle(bundle_root)
    assert "packages/widgets" not in bundle_after.concepts
    moved_doc = bundle_after.concept("packages/moved-widgets")
    assert moved_doc is not None
    assert moved_doc.fm_raw.get("version") == "0.2.0"
    assert moved_doc.fm.resource == "pkg:acme/repo-a/widgets"


def test_sync_two_repos_same_package_name_raises(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(
        graph_dir,
        [
            _RepoSeed("acme", "repo-a", packages=["shared-name"]),
            _RepoSeed("acme", "repo-b", packages=["shared-name"]),
        ],
    )
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, ["repo-a", "repo-b"], bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        with pytest.raises(ValueError, match="shared-name") as exc_info:
            sync_entities(bundle, config, reader, today=_TODAY, at=_AT)

    message = str(exc_info.value)
    assert "repo-a" in message
    assert "repo-b" in message

    # The collision check runs strictly before any disk write -- lock that
    # down with an assertion instead of relying on code-reading.
    written_md_files = list(bundle_root.rglob("*.md"))
    assert {p.relative_to(bundle_root).as_posix() for p in written_md_files} == {"index.md", "log.md"}


def test_sync_refuses_to_overwrite_unregistered_page_at_default_path(tmp_path: Path) -> None:
    """A hand-authored page with no (or an uncoercible) `resource:` is still
    a bundle member -- okf-io never raises on content -- but is invisible to
    `resource_index()`. If it already occupies the exact default path a
    graph entity would compute, `sync_entities` must refuse rather than
    silently clobber it with a fresh skeleton."""
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "repo-a", packages=["widgets"])])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, ["repo-a"], bundle_root=bundle_root)

    # Pre-place a page at the exact default path `sync_entities` would
    # compute for Package "widgets" (`packages/widgets.md`), authored before
    # anyone filled in `resource:`.
    unregistered = bundle_root / "packages" / "widgets.md"
    unregistered.parent.mkdir(parents=True, exist_ok=True)
    original_text = (
        '---\ntype: Package\ntitle: "hand-authored, not yet linked to the graph"\ndescription: ""\n---\n\n'
        "## Purpose\n\nHand-written before `resource:` was ever filled in.\n"
    )
    unregistered.write_text(original_text, encoding="utf-8")

    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        preexisting = bundle.concept("packages/widgets")
        assert preexisting is not None
        assert preexisting.fm.resource is None  # confirms it's invisible to resource_index()

        with pytest.raises(ValueError, match="packages/widgets") as exc_info:
            sync_entities(bundle, config, reader, today=_TODAY, at=_AT)

    assert "resource" in str(exc_info.value)
    # Refused, not clobbered: the file is exactly as it was.
    assert unregistered.read_text(encoding="utf-8") == original_text


def test_sync_repository_package_count_is_per_repo_not_global(tmp_path: Path) -> None:
    """Regression test for the `describe_repository()` single-repo
    assumption: repo-a has 1 package, repo-b has 2 -- each Repository page's
    `package_count` must reflect only its own repo, not the global total
    of 3."""
    graph_dir = tmp_path / "graph"
    _seed(
        graph_dir,
        [
            _RepoSeed("acme", "repo-a", packages=["one"]),
            _RepoSeed("acme", "repo-b", packages=["two", "three"]),
        ],
    )
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, ["repo-a", "repo-b"], bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        sync_entities(bundle, config, reader, today=_TODAY, at=_AT)

    bundle_after = load_bundle(bundle_root)
    repo_a_doc = bundle_after.concept("repositories/repo-a")
    repo_b_doc = bundle_after.concept("repositories/repo-b")
    assert repo_a_doc is not None and repo_a_doc.fm_raw.get("package_count") == 1
    assert repo_b_doc is not None and repo_b_doc.fm_raw.get("package_count") == 2


def test_plan_entities_reports_missing_for_a_page_that_does_not_exist_yet(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "repo-a", packages=["widgets"])])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    bundle = load_bundle(bundle_root)
    config = _config(tmp_path, graph_dir, ["repo-a"], bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(bundle, config, reader, at=_AT)

    assert "pkg:acme/repo-a/widgets" in plan.missing
    assert "pkg:acme/repo-a/widgets" not in plan.stale
    assert not (bundle_root / "packages" / "widgets.md").exists()  # nothing written


def test_plan_entities_reports_stale_for_a_page_whose_content_changed(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "repo-a", packages=["widgets"])])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, ["repo-a"], bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        sync_entities(load_bundle(bundle_root), config, reader, today=_TODAY, at=_AT)  # first sync: creates the page

    _bump_package_version(graph_dir, org="acme", repo="repo-a", name="widgets", version="0.2.0")

    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), config, reader, at=_AT)

    assert "pkg:acme/repo-a/widgets" in plan.stale
    assert "pkg:acme/repo-a/widgets" not in plan.missing


def test_plan_entities_is_silent_once_synced_and_unchanged(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "repo-a", packages=["widgets"])])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, ["repo-a"], bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        sync_result = sync_entities(load_bundle(bundle_root), config, reader, today=_TODAY, at=_AT)

    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), config, reader, at=_AT)

    assert not plan.stale
    assert not plan.missing
    assert plan.current_resources == sync_result.current_resources


def test_plan_entities_ignores_a_fresh_generated_at_timestamp(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "repo-a", packages=["widgets"])])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, ["repo-a"], bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        sync_entities(load_bundle(bundle_root), config, reader, today=_TODAY, at=_AT)

    later = datetime(2026, 6, 1, tzinfo=UTC)  # different `at`, no content change
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), config, reader, at=later)

    assert "pkg:acme/repo-a/widgets" not in plan.stale
    assert "pkg:acme/repo-a/widgets" not in plan.missing

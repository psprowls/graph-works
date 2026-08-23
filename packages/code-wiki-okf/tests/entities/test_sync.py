"""Entity planning and application through the canonical placement policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from code_graph_io import open_reader
from code_graph_io.records import GraphEdge, GraphNode, GraphRecords
from code_graph_io.testing import open_store
from code_wiki_okf.config import Config, RepoConfig, StateGateConfig
from code_wiki_okf.entities.sync import EntityPlan, EntityWrite, apply_entities, plan_entities
from code_wiki_okf.init import install_bundle
from code_wiki_okf.placement import PlacementError, canonical_member, context_from_resource
from okf_ext.writing import ApplyResult, WriteFailure
from okf_io import load_bundle

_TODAY = date(2026, 1, 1)
_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _repo_node(org: str, repo: str) -> GraphNode:
    return GraphNode(
        kind="repository",
        name=repo,
        path="",
        line=None,
        attrs={"uri": f"repo:{org}/{repo}", "owner": org, "name": repo},
    )


def _package_node(org: str, repo: str, name: str) -> GraphNode:
    return GraphNode(
        kind="package",
        name=name,
        path=f"packages/{repo}/{name}/pyproject.toml",
        line=None,
        attrs={"uri": f"pkg:{org}/{repo}/{name}", "language": "python", "version": "0.1.0"},
    )


def _app_node(org: str, repo: str, name: str) -> GraphNode:
    return GraphNode(
        kind="app",
        name=name,
        path=f"apps/{repo}/{name}/package.json",
        line=None,
        attrs={
            "uri": f"app:{org}/{repo}/{name}",
            "language": "typescript",
            "version": "1.0.0",
            "app_kind": "cli",
            "app_signals": [],
        },
    )


def _test_suite_node(org: str, repo: str, name: str) -> GraphNode:
    return GraphNode(
        kind="test_suite",
        name=name,
        path=f"{repo}/{name}",
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
        path=f"{repo}/.claude-plugin",
        line=None,
        attrs={
            "uri": f"agent_plugin:{org}/{repo}/{name}",
            "ecosystem": "claude-code",
            "version": "1.0.0",
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


def _dependency_node(ecosystem: str, name: str) -> GraphNode:
    return GraphNode(
        kind="dependency",
        name=name,
        path=None,
        line=None,
        attrs={
            "uri": f"dependency:{ecosystem}/{name}",
            "ecosystem": ecosystem,
            "versions_in_use": ["1.0"],
        },
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
    store = open_store(graph_dir / "code.db", create=True)
    try:
        for seed in repos:
            store.set_current_repo(f"repo:{seed.org}/{seed.repo}")
            nodes = [_repo_node(seed.org, seed.repo)]
            nodes += [_package_node(seed.org, seed.repo, name) for name in seed.packages]
            nodes += [_app_node(seed.org, seed.repo, name) for name in seed.apps]
            nodes += [_test_suite_node(seed.org, seed.repo, name) for name in seed.test_suites]
            nodes += [_agent_plugin_node(seed.org, seed.repo, name) for name in seed.agent_plugins]
            with store.transaction() as transaction:
                transaction.upsert_records(GraphRecords(nodes=tuple(nodes), edges=()))
        store.set_current_repo(None)
        if dependencies:
            with store.transaction() as transaction:
                transaction.upsert_records(GraphRecords(nodes=tuple(dependencies), edges=()))
    finally:
        store.close()


def _seed_dependency_edges(
    graph_dir: Path,
    *,
    dependency: tuple[str, str],
    implementations: Sequence[tuple[str, str]],
    consumer: tuple[str, str] | None = None,
) -> None:
    _ecosystem, dependency_name = dependency
    edges = [
        GraphEdge(
            src=("dependency", dependency_name, None),
            dst=("package", package_name, f"packages/{repo}/{package_name}/pyproject.toml"),
            kind="implemented_by",
            attrs={},
        )
        for repo, package_name in implementations
    ]
    if consumer is not None:
        repo, package_name = consumer
        edges.append(
            GraphEdge(
                src=("package", package_name, f"packages/{repo}/{package_name}/pyproject.toml"),
                dst=("dependency", dependency_name, None),
                kind="used_by",
                attrs={},
            )
        )
    store = open_store(graph_dir / "code.db", create=True)
    try:
        with store.transaction() as transaction:
            transaction.upsert_records(GraphRecords(nodes=(), edges=tuple(edges)))
    finally:
        store.close()


def _seed_isolated_same_name_entities(graph_dir: Path, *, org: str, repo: str, marker: str) -> None:
    package_path = f"packages/{repo}/shared/pyproject.toml"
    app_package_path = f"packages/{repo}/shared-app/package.json"
    app_path = f"apps/{repo}/shared-app/package.json"
    suite_path = f"{repo}/shared-tests"
    plugin_path = f"{repo}/.claude-plugin"
    package_file = f"src/{marker}_package.py"
    app_file = f"src/{marker}_app.ts"
    suite_file = f"tests/{marker}_test.py"
    nodes = (
        _repo_node(org, repo),
        GraphNode(
            kind="package",
            name="shared",
            path=package_path,
            line=None,
            attrs={"uri": f"pkg:{org}/{repo}/shared", "language": "python", "version": f"{marker}.0"},
        ),
        GraphNode(
            kind="package",
            name="shared-app",
            path=app_package_path,
            line=None,
            attrs={
                "uri": f"pkg:{org}/{repo}/shared-app",
                "language": "typescript",
                "version": f"{marker}.0",
            },
        ),
        GraphNode(
            kind="app",
            name="shared-app",
            path=app_path,
            line=None,
            attrs={
                "uri": f"app:{org}/{repo}/shared-app",
                "language": "typescript",
                "version": f"{marker}.0",
                "app_kind": "cli",
                "app_signals": [marker],
            },
        ),
        GraphNode(
            kind="test_suite",
            name="shared-tests",
            path=suite_path,
            line=None,
            attrs={
                "uri": f"test_suite:{org}/{repo}/shared-tests",
                "suite_kind": f"{marker}-suite",
                "path": suite_path,
                "owner_kind": "repository",
            },
        ),
        GraphNode(
            kind="agent_plugin",
            name="shared-plugin",
            path=plugin_path,
            line=None,
            attrs={
                "uri": f"agent_plugin:{org}/{repo}/shared-plugin",
                "ecosystem": "claude-code",
                "version": f"{marker}.0",
                "components": {
                    "commands": [
                        {
                            "id": f"command:{marker}",
                            "name": f"{marker}-command",
                            "description": f"{marker} command",
                        }
                    ],
                    "agents": [],
                    "skills": [],
                    "scripts": [],
                    "hooks": [],
                    "mcp_servers": [],
                },
            },
        ),
        GraphNode(
            kind="file",
            name=package_file,
            path=package_file,
            line=None,
            attrs={"uri": f"file:{org}/{repo}/{package_file}"},
        ),
        GraphNode(
            kind="file",
            name=app_file,
            path=app_file,
            line=None,
            attrs={"uri": f"file:{org}/{repo}/{app_file}"},
        ),
        GraphNode(
            kind="file",
            name=suite_file,
            path=suite_file,
            line=None,
            attrs={"uri": f"file:{org}/{repo}/{suite_file}"},
        ),
    )
    edges = (
        GraphEdge(
            src=("package", "shared", package_path),
            dst=("file", package_file, package_file),
            kind="contains",
            attrs={},
        ),
        GraphEdge(
            src=("package", "shared-app", app_package_path),
            dst=("file", app_file, app_file),
            kind="contains",
            attrs={},
        ),
        GraphEdge(
            src=("package", "shared-app", app_package_path),
            dst=("app", "shared-app", app_path),
            kind="facet_of",
            attrs={},
        ),
        GraphEdge(
            src=("test_suite", "shared-tests", suite_path),
            dst=("file", suite_file, suite_file),
            kind="physically_contains",
            attrs={},
        ),
    )
    store = open_store(graph_dir / "code.db", create=True)
    try:
        store.set_current_repo(f"repo:{org}/{repo}")
        with store.transaction() as transaction:
            transaction.upsert_records(GraphRecords(nodes=nodes, edges=edges))
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


def _installed_bundle(tmp_path: Path) -> Path:
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    return bundle_root


def _bundle_member_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_plan_builds_the_complete_canonical_entity_matrix_before_apply(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(
        graph_dir,
        [
            _RepoSeed(
                "acme",
                "demo",
                packages=("lib",),
                apps=("web",),
                test_suites=("unit",),
                agent_plugins=("reviewer",),
            )
        ],
        dependencies=(_dependency_node("pypi", "httpx"),),
    )
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    assert {write.member for write in plan.writes} == {
        "repositories/demo/repository.md",
        "repositories/demo/packages/lib.md",
        "repositories/demo/apps/web.md",
        "repositories/demo/test-suites/unit.md",
        "repositories/demo/agent-plugins/reviewer.md",
        "dependencies/pypi/httpx.md",
    }
    assert not any((bundle_root / write.member).exists() for write in plan.writes)

    summary = apply_entities(bundle_root, plan, today=_TODAY)

    assert set(summary.written) == {write.member.removesuffix(".md") for write in plan.writes}
    assert summary.warnings == ()
    assert all((bundle_root / write.member).is_file() for write in plan.writes)

    bundle = load_bundle(bundle_root)
    repository = bundle.concept("repositories/demo/repository")
    package = bundle.concept("repositories/demo/packages/lib")
    dependency = bundle.concept("dependencies/pypi/httpx")
    assert repository is not None and repository.fm_raw["package_count"] == 1
    assert package is not None and package.fm.resource == "pkg:acme/demo/lib"
    assert dependency is not None and dependency.fm_raw["ecosystem"] == "pypi"
    package_text = (bundle_root / "repositories/demo/packages/lib.md").read_text(encoding="utf-8")
    assert "## Files\n\n_(none)_" in package_text


def test_applying_one_complete_plan_twice_is_idempotent(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "demo", packages=("lib",))])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    first = apply_entities(bundle_root, plan, today=_TODAY)
    after_first = {write.member: (bundle_root / write.member).read_bytes() for write in plan.writes}
    second = apply_entities(bundle_root, plan, today=_TODAY)

    assert first.written
    assert second.written == ()
    assert {write.member: (bundle_root / write.member).read_bytes() for write in plan.writes} == after_first


def test_entity_apply_reports_generator_write_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "demo", packages=("lib",))])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())
    failure = WriteFailure(path="repositories/demo/packages/lib.md", kind="commit-error", error="disk full")
    monkeypatch.setattr(
        "code_wiki_okf.entities.sync.apply_regenerations",
        lambda *_args, **_kwargs: ApplyResult(written=(), failed=(failure,), skipped=()),
    )

    summary = apply_entities(bundle_root, plan, today=_TODAY)

    assert summary.skipped == ("repositories/demo/packages/lib.md: commit-error: disk full",)
    assert not summary.ok


def test_same_name_packages_in_two_repositories_have_distinct_members(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(
        graph_dir,
        [
            _RepoSeed("acme", "one", packages=("shared",)),
            _RepoSeed("acme", "two", packages=("shared",)),
        ],
    )
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("one", "two"), bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    package_writes = {
        write.context.resource: write.member for write in plan.writes if write.context.type_name == "Package"
    }
    assert package_writes == {
        "pkg:acme/one/shared": "repositories/one/packages/shared.md",
        "pkg:acme/two/shared": "repositories/two/packages/shared.md",
    }


def test_same_short_repository_name_with_two_resources_refuses(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(
        graph_dir,
        [
            _RepoSeed("acme", "demo"),
            _RepoSeed("other", "demo"),
        ],
    )
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        assert {str(node.attrs["uri"]) for node in reader.list_repositories()} == {
            "repo:acme/demo",
            "repo:other/demo",
        }
        with pytest.raises(PlacementError) as raised:
            plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    assert raised.value.resource in {"repo:acme/demo", "repo:other/demo"}
    assert raised.value.expected == "repositories/demo/repository"
    assert "repo:acme/demo" in raised.value.reason
    assert "repo:other/demo" in raised.value.reason
    assert not (bundle_root / "repositories/demo/repository.md").exists()


def test_same_named_entities_render_only_their_repository_metadata(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed_isolated_same_name_entities(graph_dir, org="acme", repo="one", marker="one")
    _seed_isolated_same_name_entities(graph_dir, org="acme", repo="two", marker="two")
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("one", "two"), bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())
    apply_entities(bundle_root, plan, today=_TODAY)

    bundle = load_bundle(bundle_root)
    one_package = bundle.concept("repositories/one/packages/shared")
    two_package = bundle.concept("repositories/two/packages/shared")
    one_suite = bundle.concept("repositories/one/test-suites/shared-tests")
    two_suite = bundle.concept("repositories/two/test-suites/shared-tests")
    one_plugin = bundle.concept("repositories/one/agent-plugins/shared-plugin")
    two_plugin = bundle.concept("repositories/two/agent-plugins/shared-plugin")
    assert one_package is not None and one_package.fm_raw["version"] == "one.0"
    assert two_package is not None and two_package.fm_raw["version"] == "two.0"
    assert "one_package.py" in one_package.body and "two_package.py" not in one_package.body
    assert "two_package.py" in two_package.body and "one_package.py" not in two_package.body
    assert one_suite is not None and one_suite.fm_raw["suite_kind"] == "one-suite"
    assert two_suite is not None and two_suite.fm_raw["suite_kind"] == "two-suite"
    assert "one_test.py" in one_suite.body and "two_test.py" not in one_suite.body
    assert "two_test.py" in two_suite.body and "one_test.py" not in two_suite.body
    assert one_plugin is not None and one_plugin.fm_raw["version"] == "one.0"
    assert two_plugin is not None and two_plugin.fm_raw["version"] == "two.0"
    assert "one-command" in one_plugin.body and "two-command" not in one_plugin.body
    assert "two-command" in two_plugin.body and "one-command" not in two_plugin.body

    one_app = bundle.concept("repositories/one/apps/shared-app")
    two_app = bundle.concept("repositories/two/apps/shared-app")
    assert one_app is not None and "one_app.ts" in one_app.body and "two_app.ts" not in one_app.body
    assert two_app is not None and "two_app.ts" in two_app.body and "one_app.ts" not in two_app.body


def test_existing_resource_at_wrong_member_refuses_without_duplicate(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "demo")])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)
    wrong = bundle_root / "repositories" / "demo.md"
    wrong.parent.mkdir(parents=True, exist_ok=True)
    wrong.write_text("---\ntype: Repository\ntitle: demo\nresource: repo:acme/demo\n---\n", encoding="utf-8")

    with open_reader(graph_dir=graph_dir) as reader, pytest.raises(PlacementError) as raised:
        plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    assert raised.value.resource == "repo:acme/demo"
    assert raised.value.expected == "repositories/demo/repository"
    assert "repositories/demo.md" in raised.value.reason
    assert "delete" in raised.value.reason and "regenerate" in raised.value.reason
    assert not (bundle_root / "repositories" / "demo" / "repository.md").exists()


def test_duplicate_resources_refuse_before_any_entity_write(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "demo", packages=("lib",))])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)
    for relative in ("old/first.md", "old/second.md"):
        path = bundle_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\ntype: Package\ntitle: lib\nresource: pkg:acme/demo/lib\n---\n",
            encoding="utf-8",
        )

    with open_reader(graph_dir=graph_dir) as reader, pytest.raises(PlacementError) as raised:
        plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    assert raised.value.resource == "pkg:acme/demo/lib"
    assert "old/first.md" in raised.value.reason
    assert "old/second.md" in raised.value.reason
    assert not (bundle_root / "repositories/demo/packages/lib.md").exists()


def test_unregistered_page_at_a_canonical_target_refuses(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "demo", packages=("lib",))])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)
    occupied = bundle_root / "repositories/demo/packages/lib.md"
    occupied.parent.mkdir(parents=True, exist_ok=True)
    before = "---\ntype: Package\ntitle: hand-authored\n---\n\nDo not overwrite.\n"
    occupied.write_text(before, encoding="utf-8")

    with open_reader(graph_dir=graph_dir) as reader, pytest.raises(PlacementError, match="no matching resource"):
        plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    assert occupied.read_text(encoding="utf-8") == before


def test_entity_plan_and_frontmatter_are_immutable(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "demo", packages=("lib",))])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    write = next(item for item in plan.writes if item.context.type_name == "Package")
    assert isinstance(write, EntityWrite)
    with pytest.raises(TypeError):
        write.frontmatter["title"] = "changed"  # type: ignore[index]
    generated = write.frontmatter["generated"]
    assert isinstance(generated, Mapping)
    with pytest.raises(TypeError):
        generated["by"] = "changed"  # type: ignore[index]
    depends_on = write.frontmatter["depends_on"]
    assert isinstance(depends_on, tuple)
    with pytest.raises(AttributeError):
        depends_on.append("pkg:other/repo/mutated")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        plan.writes = ()  # type: ignore[misc]


def test_dependency_preserves_multiple_implementations_and_warns(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(
        graph_dir,
        [
            _RepoSeed("acme", "one", packages=("first",)),
            _RepoSeed("acme", "two", packages=("second",)),
        ],
        dependencies=(_dependency_node("pypi", "shared"),),
    )
    _seed_dependency_edges(
        graph_dir,
        dependency=("pypi", "shared"),
        implementations=(("two", "second"), ("one", "first")),
    )
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("one", "two"), bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    dependency = next(write for write in plan.writes if write.context.resource == "dependency:pypi/shared")
    assert dependency.frontmatter["implemented_by"] == ("pkg:acme/one/first", "pkg:acme/two/second")
    assert plan.warnings == (
        "dependency:pypi/shared has multiple implementations: pkg:acme/one/first, pkg:acme/two/second",
    )

    summary = apply_entities(bundle_root, plan, today=_TODAY)
    assert summary.warnings == plan.warnings
    document = load_bundle(bundle_root).concept("dependencies/pypi/shared")
    assert document is not None
    assert document.fm_raw["implemented_by"] == ["pkg:acme/one/first", "pkg:acme/two/second"]


def test_dependency_with_a_consumer_and_no_implementation_remains(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(
        graph_dir,
        [_RepoSeed("acme", "demo", packages=("consumer",))],
        dependencies=(_dependency_node("pypi", "external"),),
    )
    _seed_dependency_edges(
        graph_dir,
        dependency=("pypi", "external"),
        implementations=(),
        consumer=("demo", "consumer"),
    )
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    dependency = next(write for write in plan.writes if write.context.resource == "dependency:pypi/external")
    assert dependency.frontmatter["used_by"] == ("consumer",)
    assert dependency.frontmatter["implemented_by"] == ()
    assert plan.warnings == ()


def test_dependency_absent_from_the_reconciled_graph_is_not_planned(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "demo")])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    assert not [write for write in plan.writes if write.context.type_name == "Dependency"]


@pytest.mark.parametrize("at", ("not-a-timestamp", "2026-01-01T00:00:00"))
def test_entity_planning_refuses_invalid_or_naive_timestamps_before_writes(tmp_path: Path, at: str) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "demo")])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader, pytest.raises(ValueError, match=r"ISO-8601|timezone offset"):
        plan_entities(load_bundle(bundle_root), reader, config, at=at)

    assert not (bundle_root / "repositories/demo/repository.md").exists()


def test_unmatched_configured_repository_produces_no_entity_writes(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "other")])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("missing",), bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    assert plan.writes == ()
    assert plan.current_resources == frozenset()


def test_existing_canonical_resource_with_the_wrong_type_is_refused(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "demo", packages=("lib",))])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)
    occupied = bundle_root / "repositories/demo/packages/lib.md"
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_text(
        "---\ntype: App\ntitle: lib\nresource: pkg:acme/demo/lib\n---\n",
        encoding="utf-8",
    )

    with open_reader(graph_dir=graph_dir) as reader, pytest.raises(PlacementError, match="declares type App"):
        plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())


def test_entity_apply_revalidates_canonical_members_before_writes(tmp_path: Path) -> None:
    bundle_root = _installed_bundle(tmp_path)
    context = context_from_resource("Package", "pkg:acme/demo/lib")
    plan = EntityPlan(
        writes=(EntityWrite(context=context, member="packages/lib.md", frontmatter={}),),
        current_resources=frozenset({context.resource}),
        warnings=(),
    )

    with pytest.raises(PlacementError, match="is not canonical"):
        apply_entities(bundle_root, plan, today=_TODAY)

    assert not (bundle_root / "packages/lib.md").exists()


def test_entity_apply_refuses_two_resources_with_the_same_canonical_member(tmp_path: Path) -> None:
    bundle_root = _installed_bundle(tmp_path)
    first = context_from_resource("Package", "pkg:acme/demo/@acme/web")
    second = context_from_resource("Package", "pkg:acme/demo/@acme__web")
    member = canonical_member(first)
    assert canonical_member(second) == member
    plan = EntityPlan(
        writes=(
            EntityWrite(context=first, member=member, frontmatter={}),
            EntityWrite(context=second, member=member, frontmatter={}),
        ),
        current_resources=frozenset({first.resource, second.resource}),
        warnings=(),
    )

    with pytest.raises(PlacementError, match="also belongs"):
        apply_entities(bundle_root, plan, today=_TODAY)

    assert not (bundle_root / member).exists()


def test_entity_plan_refuses_filesystem_equivalent_canonical_members(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "demo", packages=("Widget", "widget"))])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader, pytest.raises(PlacementError, match="filesystem-equivalent"):
        plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    assert not (bundle_root / "repositories/demo/packages/Widget.md").exists()
    assert not (bundle_root / "repositories/demo/packages/widget.md").exists()


def test_entity_apply_refuses_filesystem_equivalent_tampered_targets_before_writes(tmp_path: Path) -> None:
    bundle_root = _installed_bundle(tmp_path)
    first = context_from_resource("Package", "pkg:acme/demo/Widget")
    second = context_from_resource("Package", "pkg:acme/demo/widget")
    plan = EntityPlan(
        writes=(
            EntityWrite(context=first, member=canonical_member(first), frontmatter={}),
            EntityWrite(context=second, member=canonical_member(second), frontmatter={}),
        ),
        current_resources=frozenset({first.resource, second.resource}),
        warnings=(),
    )

    with pytest.raises(PlacementError, match="filesystem-equivalent"):
        apply_entities(bundle_root, plan, today=_TODAY)

    assert not (bundle_root / canonical_member(first)).exists()
    assert not (bundle_root / canonical_member(second)).exists()


def test_entity_apply_refuses_late_filesystem_equivalent_occupant(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "demo", packages=("widget",))])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())
    occupied = bundle_root / "repositories" / "demo" / "packages" / "WIDGET.md"
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_text("---\ntype: Note\ntitle: occupied\n---\n", encoding="utf-8")
    before = occupied.read_bytes()

    with pytest.raises(PlacementError, match="filesystem-equivalent"):
        apply_entities(bundle_root, plan, today=_TODAY)

    assert occupied.read_bytes() == before


def test_entity_apply_refuses_case_equivalent_ancestor_file_before_any_write(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, [_RepoSeed("acme", "demo", packages=("lib",))])
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    assert len(plan.writes) > 1
    occupied = bundle_root / "repositories" / "demo" / "PACKAGES"
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_bytes(b"pre-existing ancestor file\n")
    before = _bundle_member_bytes(bundle_root)

    with pytest.raises(PlacementError, match="filesystem-equivalent"):
        apply_entities(bundle_root, plan, today=_TODAY)

    assert _bundle_member_bytes(bundle_root) == before
    assert all(not (bundle_root / write.member).exists() for write in plan.writes)


@pytest.mark.parametrize(
    ("method", "expected_resource"),
    [
        ("describe_package", "pkg:acme/demo/lib"),
        ("describe_app", "app:acme/demo/web"),
        ("describe_test_suite", "test_suite:acme/demo/unit"),
        ("describe_agent_plugin", "agent_plugin:acme/demo/reviewer"),
        ("describe_dependency", "dependency:pypi/httpx"),
    ],
)
def test_plan_refuses_a_graph_node_it_cannot_describe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    expected_resource: str,
) -> None:
    """`list_*` and `describe_*` disagreeing is an invariant violation.

    It must fail the plan, never silently drop the entity: a dropped entity
    leaves `current_resources`, which makes its existing page a prune
    candidate, so the old behaviour laundered a broken graph into a deleted
    page.
    """
    graph_dir = tmp_path / "graph"
    _seed(
        graph_dir,
        [
            _RepoSeed(
                "acme",
                "demo",
                packages=("lib",),
                apps=("web",),
                test_suites=("unit",),
                agent_plugins=("reviewer",),
            )
        ],
        dependencies=(_dependency_node("pypi", "httpx"),),
    )
    bundle_root = _installed_bundle(tmp_path)
    config = _config(tmp_path, graph_dir, ("demo",), bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        monkeypatch.setattr(reader, method, lambda **_kwargs: None)
        with pytest.raises(PlacementError, match="cannot describe it") as excinfo:
            plan_entities(load_bundle(bundle_root), reader, config, at=_AT.isoformat())

    assert expected_resource in str(excinfo.value)

"""A repository-scoped sync never plans, writes or prunes another repository's pages."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from code_graph_io import open_reader
from code_graph_io.update import run_workspace
from code_wiki_okf.config import Config, RepoConfig, StateGateConfig
from code_wiki_okf.init import install_bundle
from code_wiki_okf.sync import SyncResult, plan_sync, sync_bundle

_AT = "2026-01-01T00:00:00+00:00"
_TODAY = date(2026, 1, 1)
_PACKAGES = {"one": "alpha", "two": "beta"}
#: Shared indexes list one row per repository; a scoped sync may reconcile them,
#: but with no repository added or removed they must stay byte-identical.
_SHARED_INDEXES = ("index.md", "code-graph/index.md")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _tree_bytes(directory: Path) -> dict[str, bytes]:
    return {path.as_posix(): path.read_bytes() for path in sorted(directory.rglob("*")) if path.is_file()}


def _repository_bytes(bundle_root: Path, repo: str) -> dict[str, bytes]:
    """Everything repository *repo* owns: its page, its subtree, and the shared index files."""
    owned = _tree_bytes(bundle_root / "code-graph" / repo)
    owned[f"code-graph/{repo}.md"] = (bundle_root / "code-graph" / f"{repo}.md").read_bytes()
    for member in _SHARED_INDEXES:
        owned[member] = (bundle_root / member).read_bytes()
    return owned


def _generated_package_page(repo: str, name: str) -> str:
    return (
        "---\n"
        "type: Package\n"
        f'title: "{name}"\n'
        f'resource: "pkg:acme/{repo}/{name}"\n'
        'description: ""\n'
        "generated:\n"
        "  by: code-wiki-okf/0\n"
        f"  at: '{_AT}'\n"
        "---\n\n"
        "## Purpose\n\n"
        "> TODO: what this package does, who uses it, and why it exists, in one paragraph.\n\n"
        "## Public API\n\n"
        "> TODO: the main exports and when to use them. Link code with backticked `path:line` references.\n"
    )


def _config(bundle_root: Path, graph_dir: Path, repo_roots: tuple[Path, ...]) -> Config:
    return Config(
        graph_dir=graph_dir,
        declarations_dir=bundle_root,
        repos=tuple(RepoConfig(name=root.name, path=root, ignore=()) for root in repo_roots),
        state_gate=StateGateConfig(enabled=False, branches=()),
    )


def _sync(
    bundle_root: Path, config: Config, *, dry_run: bool = False, repos: frozenset[str] | None = None
) -> SyncResult:
    with open_reader(graph_dir=config.graph_dir) as reader:
        return sync_bundle(
            bundle_root, config=config, reader=reader, at=_AT, today=_TODAY, dry_run=dry_run, repos=repos
        )


def _two_repo_synced(tmp_path: Path) -> tuple[Path, Config]:
    """Git checkouts ``one`` and ``two``, graphed and fully synced once."""
    roots: list[Path] = []
    for repo, package in _PACKAGES.items():
        root = tmp_path / repo
        (root / "src").mkdir(parents=True)
        (root / "lib").mkdir()
        (root / "pyproject.toml").write_text(
            f'[project]\nname = "{package}"\nversion = "0.1.0"\ndependencies = []\n', encoding="utf-8"
        )
        (root / "src" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "lib" / "old.py").write_text("OLD = 1\n", encoding="utf-8")
        _git(root, "init", "-q", "-b", "main")
        _git(root, "config", "user.email", "t@t")
        _git(root, "config", "user.name", "t")
        _git(root, "remote", "add", "origin", f"https://github.com/acme/{repo}.git")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "init")
        roots.append(root)
    graph_dir = tmp_path / "graph"
    run_workspace(roots, graph_dir=graph_dir, full=True)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(bundle_root, graph_dir, tuple(roots))
    assert _sync(bundle_root, config).ok
    return bundle_root, config


def _change_both(config: Config) -> None:
    """Give every repository work of each kind: an entity, a File create, a File delete, a vanished folder."""
    for repo in config.repos:
        package = _PACKAGES[repo.name]
        (repo.path / "pyproject.toml").write_text(
            f'[project]\nname = "{package}"\nversion = "0.2.0"\ndependencies = ["requests>=2"]\n',
            encoding="utf-8",
        )
        (repo.path / "src" / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
        _git(repo.path, "rm", "-q", "lib/old.py")
        _git(repo.path, "add", "-A")
        _git(repo.path, "commit", "-q", "-m", "change")
    run_workspace([repo.path for repo in config.repos], graph_dir=config.graph_dir, full=True)


def _plant_stale_pages(bundle_root: Path, repo: str) -> tuple[Path, Path]:
    """A generated page whose resource left the graph, and one whose resource cannot be parsed."""
    stale = bundle_root / f"code-graph/{repo}/entities/packages/gone.md"
    stale.write_text(_generated_package_page(repo, "gone"), encoding="utf-8", newline="")
    broken = bundle_root / f"code-graph/{repo}/entities/packages/broken.md"
    broken.write_text(
        _generated_package_page(repo, "broken").replace(f"pkg:acme/{repo}/broken", "pkg:unparseable"),
        encoding="utf-8",
        newline="",
    )
    return stale, broken


def test_unscoped_sync_touches_every_repository(tmp_path: Path) -> None:
    """The control: without a scope, repository two sees all four kinds of work."""
    bundle_root, config = _two_repo_synced(tmp_path)
    stale, broken = _plant_stale_pages(bundle_root, "two")
    entities_index = (bundle_root / "code-graph/two/entities/index.md").read_bytes()
    _change_both(config)

    result = _sync(bundle_root, config)

    assert result.ok
    written = set(result.entities.written)
    assert "code-graph/two/entities/packages/beta" in written  # entity writes
    assert {plan.repo for plan in result.mirror.plans} == {"one", "two"}  # mirror
    assert (bundle_root / "code-graph/two/file-system/src/extra.py.md").exists()
    assert not (bundle_root / "code-graph/two/file-system/lib/old.py.md").exists()
    assert not stale.exists()  # entity prune
    assert not broken.exists()
    assert "code-graph/two/file-system/lib/index.md" in result.indexes.deleted  # index prune
    assert "code-graph/two/entities/dependencies/pypi/index.md" in result.entities.catalog_created  # catalogs
    assert (bundle_root / "code-graph/two/entities/index.md").read_bytes() != entities_index


def test_scoped_sync_leaves_other_repository_byte_identical(tmp_path: Path) -> None:
    bundle_root, config = _two_repo_synced(tmp_path)
    stale, broken = _plant_stale_pages(bundle_root, "two")
    two_before = _repository_bytes(bundle_root, "two")
    _change_both(config)

    result = _sync(bundle_root, config, repos=frozenset({"one"}))

    assert result.ok
    # Repository one got every kind of work.
    assert "code-graph/one/entities/packages/alpha" in result.entities.written
    assert (bundle_root / "code-graph/one/file-system/src/extra.py.md").exists()
    assert not (bundle_root / "code-graph/one/file-system/lib/old.py.md").exists()
    assert result.indexes.deleted == ("code-graph/one/file-system/lib/index.md",)
    # Repository two got none, including the pages a full sync would prune.
    assert all(not path.startswith("code-graph/two") for path in result.entities.written)
    assert {plan.repo for plan in result.mirror.plans} == {"one"}
    assert result.mirror.skipped_repos == ()
    assert result.entities.deleted == ()
    assert all(not path.startswith("code-graph/two") for path in result.entities.catalog)
    assert stale.exists() and broken.exists()
    assert _repository_bytes(bundle_root, "two") == two_before


def test_scoped_dry_run_plans_only_the_scoped_repository(tmp_path: Path) -> None:
    bundle_root, config = _two_repo_synced(tmp_path)
    _plant_stale_pages(bundle_root, "one")
    _change_both(config)
    before = _tree_bytes(bundle_root)

    preview = _sync(bundle_root, config, dry_run=True, repos=frozenset({"two"}))

    assert _tree_bytes(bundle_root) == before
    touched = [
        *preview.entities.created,
        *preview.entities.updated,
        *preview.entities.deleted,
        *preview.entities.catalog,
        *preview.indexes.deleted,
    ]
    assert touched
    assert all(path.startswith("code-graph/two") or path in _SHARED_INDEXES for path in touched)
    assert {plan.repo for plan in preview.mirror.plans} == {"two"}

    live = _sync(bundle_root, config, repos=frozenset({"two"}))
    assert live.ok
    assert preview.entities.deleted == live.entities.deleted
    assert preview.entities.catalog_created == live.entities.catalog_created
    assert preview.entities.catalog_updated == live.entities.catalog_updated
    assert preview.indexes == live.indexes
    assert (bundle_root / "code-graph/one/entities/packages/gone.md").exists()


def test_scoped_plan_writes_only_the_scoped_repository(tmp_path: Path) -> None:
    bundle_root, config = _two_repo_synced(tmp_path)
    _change_both(config)
    with open_reader(graph_dir=config.graph_dir) as reader:
        plan = plan_sync(bundle_root, config=config, reader=reader, at=_AT, repos=frozenset({"one"}))
        unscoped = plan_sync(bundle_root, config=config, reader=reader, at=_AT)

    assert plan.entities.writes
    assert {write.context.repository for write in plan.entities.writes} == {"one"}
    assert [mirror.repo for mirror in plan.mirrors] == ["one"]
    # Repository two's existing page is kept current, so pruning never sees it.
    assert "file:acme/two/lib/old.py" in plan.entities.current_resources
    assert "file:acme/two/lib/old.py" not in unscoped.entities.current_resources

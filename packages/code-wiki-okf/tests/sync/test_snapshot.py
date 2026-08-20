from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from code_graph_io import open_reader
from code_graph_io import update as graph_update
from code_graph_io.records import GraphNode, GraphRecords
from code_graph_io.testing import open_store
from code_wiki_okf.config import Config, RepoConfig, StateGateConfig
from code_wiki_okf.entities.sync import sync_entities
from code_wiki_okf.init import install_bundle
from code_wiki_okf.sync.snapshot import snapshot_bundle
from okf_io import load_bundle

_TODAY = date(2026, 1, 1)
_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def mirror_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo-a"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "mod.py").write_text("VALUE = 1\n")
    _git(["init", "-q", "-b", "main"], repo_root)
    _git(["config", "user.email", "t@t"], repo_root)
    _git(["config", "user.name", "t"], repo_root)
    _git(["add", "-A"], repo_root)
    _git(["commit", "-q", "-m", "init"], repo_root)
    return repo_root


@pytest.fixture
def graph_dir(tmp_path: Path, mirror_repo: Path) -> Path:
    graph_dir = tmp_path / "graph"
    graph_update.run_workspace([mirror_repo], graph_dir=graph_dir, full=True)
    return graph_dir


def _config(tmp_path: Path, graph_dir: Path, repo_path: Path, *, bundle_root: Path) -> Config:
    return Config(
        graph_dir=graph_dir,
        declarations_dir=bundle_root,
        repos=(RepoConfig(name="repo-a", path=repo_path, ignore=()),),
        state_gate=StateGateConfig(enabled=False, branches=()),
    )


def _repo_node(org: str, repo: str) -> GraphNode:
    return GraphNode(
        kind="repository",
        name=repo,
        path="",
        line=None,
        attrs={"uri": f"repo:{org}/{repo}", "owner": org, "name": repo, "url": "", "default_branch": "main"},
    )


def _package_node(org: str, repo: str, name: str) -> GraphNode:
    return GraphNode(
        kind="package",
        name=name,
        path=f"packages/{name}/pyproject.toml",
        line=None,
        attrs={"uri": f"pkg:{org}/{repo}/{name}", "language": "python", "version": "0.1.0"},
    )


def _seed_repo(db_path: Path, org: str, repo: str, *, packages: Sequence[str] = ()) -> None:
    """Write `db_path` with one Repository node plus any named Package nodes,
    matching `entities/test_sync.py`'s `_RepoSeed`/`_seed` shapes but scoped
    down to what the Package-lane orphan test below needs."""
    store = open_store(db_path, create=True)
    try:
        store.set_current_repo(f"repo:{org}/{repo}")
        nodes: list[GraphNode] = [_repo_node(org, repo)]
        nodes += [_package_node(org, repo, name) for name in packages]
        with store.transaction() as tx:
            tx.upsert_records(GraphRecords(nodes=tuple(nodes), edges=()))
        store.set_current_repo(None)
    finally:
        store.close()


def test_package_lane_orphan_is_detected_after_removal_from_graph(tmp_path: Path) -> None:
    """`_existing_entity_resources` (`sync/snapshot.py`) folds every entity
    lane's prefix into one combined orphan check, but until now only the
    `repositories/` prefix (Repository entity vs. mirror File pages, see
    `test_repository_entity_page_is_never_miscounted_as_orphaned_mirror_page`
    above) had end-to-end coverage. This proves the plain-prefix `packages/`
    path directly: a Package page synced once must be reported orphaned once
    its source node is gone from a later graph read.

    "Gone from the graph" is a second, package-less graph rather than an
    in-place row delete on the first -- `GraphStore`'s public write surface
    (`upsert_records`) is insert/update only, and reaching around it for a
    raw `DELETE` would violate code-graph-io's own DB-boundary invariant.
    A fresh graph the reader opens next is observably identical to the node
    having disappeared from this run's scan.
    """
    graph_dir = tmp_path / "graph"
    _seed_repo(graph_dir / "code.db", "acme", "repo-a", packages=["widgets"])

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = Config(
        graph_dir=graph_dir,
        declarations_dir=bundle_root,
        repos=(RepoConfig(name="repo-a", path=tmp_path / "repo-a", ignore=()),),
        state_gate=StateGateConfig(enabled=False, branches=()),
    )

    with open_reader(graph_dir=graph_dir) as reader:
        sync_entities(load_bundle(bundle_root), config, reader, today=_TODAY, at=_AT)

    package_doc = load_bundle(bundle_root).concept("packages/widgets")
    assert package_doc is not None
    package_resource = package_doc.fm.resource
    assert package_resource is not None

    graph_dir_after = tmp_path / "graph-after"
    _seed_repo(graph_dir_after / "code.db", "acme", "repo-a", packages=())
    config_after = Config(
        graph_dir=graph_dir_after,
        declarations_dir=config.declarations_dir,
        repos=config.repos,
        state_gate=config.state_gate,
    )

    with open_reader(graph_dir=graph_dir_after) as reader:
        snapshot = snapshot_bundle(load_bundle(bundle_root), config_after, reader, at=_AT)

    assert package_resource in snapshot.orphaned


def test_untouched_bundle_reports_the_tracked_file_as_missing(
    tmp_path: Path, mirror_repo: Path, graph_dir: Path
) -> None:
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, mirror_repo, bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        snapshot = snapshot_bundle(load_bundle(bundle_root), config, reader, at=_AT)

    assert "file:repo-a/src/mod.py" in snapshot.missing
    assert not snapshot.stale
    assert not snapshot.orphaned
    assert not any(bundle_root.rglob("mod.py.md"))  # nothing written


def test_synced_bundle_is_silent(tmp_path: Path, mirror_repo: Path, graph_dir: Path) -> None:
    from code_wiki_okf.git_state import head_commit
    from code_wiki_okf.mirror.apply import apply_mirror
    from code_wiki_okf.mirror.plan import plan_mirror
    from code_wiki_okf.mirror.walk import tracked_files
    from okf_ext.shape import load_sections

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, mirror_repo, bundle_root=bundle_root)
    section_set = load_sections(bundle_root / "_sections")

    with open_reader(graph_dir=graph_dir) as reader:
        walked = tracked_files(config)
        repo = config.repos[0]
        sha = head_commit(repo.path)
        assert sha is not None
        plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=walked[repo.name], sha=sha, at=_AT)
        apply_mirror(load_bundle(bundle_root), plan, repo, section_set=section_set)
        # "synced" means both lanes -- the entity lane (here, just repo-a's
        # own Repository page) also has to land, or its resource shows up in
        # `.missing` forever, since nothing else in this test ever creates it.
        sync_entities(load_bundle(bundle_root), config, reader, today=_TODAY, at=_AT)

    with open_reader(graph_dir=graph_dir) as reader:
        snapshot = snapshot_bundle(load_bundle(bundle_root), config, reader, at=_AT)

    assert not snapshot.missing
    assert not snapshot.stale
    assert not snapshot.orphaned


def test_orphan_after_source_removed(tmp_path: Path, mirror_repo: Path, graph_dir: Path) -> None:
    from code_wiki_okf.git_state import head_commit
    from code_wiki_okf.mirror.apply import apply_mirror
    from code_wiki_okf.mirror.plan import plan_mirror
    from code_wiki_okf.mirror.walk import tracked_files
    from okf_ext.shape import load_sections

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, mirror_repo, bundle_root=bundle_root)
    section_set = load_sections(bundle_root / "_sections")

    with open_reader(graph_dir=graph_dir) as reader:
        walked = tracked_files(config)
        repo = config.repos[0]
        sha = head_commit(repo.path)
        assert sha is not None
        plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=walked[repo.name], sha=sha, at=_AT)
        apply_mirror(load_bundle(bundle_root), plan, repo, section_set=section_set)

    (mirror_repo / "src" / "mod.py").unlink()
    _git(["add", "-A"], mirror_repo)
    _git(["commit", "-q", "-m", "remove mod.py"], mirror_repo)

    with open_reader(graph_dir=graph_dir) as reader:
        snapshot = snapshot_bundle(load_bundle(bundle_root), config, reader, at=_AT)

    assert "file:repo-a/src/mod.py" in snapshot.orphaned


def test_prose_edited_orphan_is_reported_even_though_apply_declines_to_delete_it(
    tmp_path: Path, mirror_repo: Path, graph_dir: Path
) -> None:
    """`plan_mirror` routes a page with real (non-placeholder) `## Notes`
    prose into `declined_deletions` rather than `deletions` -- `apply_mirror`
    will never auto-delete it. The snapshot's `.orphaned` set must still flag
    it: the page's source is gone and it needs a human's attention, even
    though the write path is deliberately more conservative than the report.
    """
    from code_wiki_okf.git_state import head_commit
    from code_wiki_okf.mirror.apply import apply_mirror
    from code_wiki_okf.mirror.plan import plan_mirror
    from code_wiki_okf.mirror.walk import tracked_files
    from okf_ext.shape import load_sections

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, mirror_repo, bundle_root=bundle_root)
    section_set = load_sections(bundle_root / "_sections")

    with open_reader(graph_dir=graph_dir) as reader:
        walked = tracked_files(config)
        repo = config.repos[0]
        sha = head_commit(repo.path)
        assert sha is not None
        plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=walked[repo.name], sha=sha, at=_AT)
        apply_mirror(load_bundle(bundle_root), plan, repo, section_set=section_set)

    page_path = bundle_root / "repositories" / "repo-a" / "fs" / "src" / "mod.py.md"
    assert page_path.exists()
    placeholder = (
        "> TODO: anything a reader should know about this file that the generated sections below don't capture."
    )
    text = page_path.read_text()
    assert placeholder in text
    page_path.write_text(text.replace(placeholder, "> Hand-written notes a human added about this file."))

    (mirror_repo / "src" / "mod.py").unlink()
    _git(["add", "-A"], mirror_repo)
    _git(["commit", "-q", "-m", "remove mod.py"], mirror_repo)

    with open_reader(graph_dir=graph_dir) as reader:
        snapshot = snapshot_bundle(load_bundle(bundle_root), config, reader, at=_AT)

    assert "file:repo-a/src/mod.py" in snapshot.orphaned


def test_repository_entity_page_is_never_miscounted_as_orphaned_mirror_page(
    tmp_path: Path, mirror_repo: Path, graph_dir: Path
) -> None:
    """`repositories/repo-a` (the Repository entity page) and
    `repositories/repo-a/fs/src/mod.py` (a mirror File page) coexist under the
    same `repositories/` prefix. Removing the mirrored file's *source* must
    orphan only the mirror page, never the Repository entity page whose own
    resource (`repo:...`) is unrelated and still current. This is the
    guarantee `_is_entity_repository_page`'s depth check exists for.
    """
    from code_wiki_okf.git_state import head_commit
    from code_wiki_okf.mirror.apply import apply_mirror
    from code_wiki_okf.mirror.plan import plan_mirror
    from code_wiki_okf.mirror.walk import tracked_files
    from okf_ext.shape import load_sections

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, mirror_repo, bundle_root=bundle_root)
    section_set = load_sections(bundle_root / "_sections")

    with open_reader(graph_dir=graph_dir) as reader:
        walked = tracked_files(config)
        repo = config.repos[0]
        sha = head_commit(repo.path)
        assert sha is not None
        plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=walked[repo.name], sha=sha, at=_AT)
        apply_mirror(load_bundle(bundle_root), plan, repo, section_set=section_set)
        sync_entities(load_bundle(bundle_root), config, reader, today=_TODAY, at=_AT)

    after_sync = load_bundle(bundle_root)
    assert (bundle_root / "repositories" / "repo-a.md").exists()
    repo_resource = after_sync.concepts["repositories/repo-a"].fm.resource
    assert repo_resource is not None

    # Both lanes are fully synced and the source file still exists: nothing
    # should be orphaned yet. This is the "vice versa" half of the guarantee
    # -- if the depth check were instead too permissive (treating every
    # `repositories/repo-a/**` mirror page as an entity resource too), the
    # still-current mirror File page would be wrongly folded into
    # `_existing_entity_resources` and then subtracted against
    # `entity_plan.current_resources` (which knows nothing about mirror
    # resources), reporting it as orphaned despite its source being intact.
    with open_reader(graph_dir=graph_dir) as reader:
        pre_removal_snapshot = snapshot_bundle(load_bundle(bundle_root), config, reader, at=_AT)
    assert not pre_removal_snapshot.orphaned

    (mirror_repo / "src" / "mod.py").unlink()
    _git(["add", "-A"], mirror_repo)
    _git(["commit", "-q", "-m", "remove mod.py"], mirror_repo)

    with open_reader(graph_dir=graph_dir) as reader:
        snapshot = snapshot_bundle(load_bundle(bundle_root), config, reader, at=_AT)

    assert "file:repo-a/src/mod.py" in snapshot.orphaned
    assert repo_resource not in snapshot.orphaned

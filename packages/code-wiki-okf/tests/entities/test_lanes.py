"""Tests for the top-level `sync()`: sync_entities + prune_lane per lane,
touched-lane index reconciliation, and one log.md entry per run.

Fixture graphs are seeded the same way `test_sync.py` does: directly through
`code_graph_io.testing.open_store` (a writable `GraphStore` on an arbitrary
db path). `code_graph_io.testing` carries no `build_records` helper and
`code_graph_io` carries no `open_writer` -- both appear in an earlier plan
draft but neither exists; `open_store` + `GraphRecords` is what the real
package offers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from code_graph_io import open_reader
from code_graph_io.records import GraphNode, GraphRecords
from code_graph_io.testing import open_store
from code_wiki_okf.config import Config, RepoConfig, StateGateConfig
from code_wiki_okf.entities.catalog import render_repositories, repository_entries
from code_wiki_okf.entities.lanes import SyncSummary, placement_directories, sync
from code_wiki_okf.init import install_bundle
from okf_ext.generators import Render, plan_regenerate
from okf_ext.schemas import declared_directories, load_schemas
from okf_ext.shape import load_sections
from okf_io import load_bundle

_TODAY = date(2026, 1, 1)
_AT = datetime(2026, 1, 1, tzinfo=UTC)

#: Same convention as `tests/entities/test_pages.py`: the package's own
#: seeded schema declarations, read directly rather than through
#: `install_bundle`, since `placement_directories` needs nothing but a
#: `SchemaSet`.
_ASSETS = Path(__file__).parents[2] / "src" / "code_wiki_okf" / "assets"


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


def _seed(graph_dir: Path, org: str, repo: str, packages: Sequence[str]) -> None:
    """Write `graph_dir/code.db` with exactly the given packages for one
    repo. `upsert_records` is additive only -- it never removes a node a
    prior call inserted -- so simulating "a package disappeared from the
    graph" between two `sync()` runs needs a **fresh** `graph_dir` for the
    second seed, not a second call against the same one; every caller in
    this module follows that rule."""
    store = open_store(graph_dir / "code.db", create=True)
    try:
        store.set_current_repo(f"repo:{org}/{repo}")
        nodes: list[GraphNode] = [_repo_node(org, repo)]
        nodes += [_package_node(org, repo, name) for name in packages]
        with store.transaction() as tx:
            tx.upsert_records(GraphRecords(nodes=tuple(nodes), edges=()))
        store.set_current_repo(None)
    finally:
        store.close()


def _config(tmp_path: Path, graph_dir: Path, repo_name: str, *, bundle_root: Path) -> Config:
    return Config(
        graph_dir=graph_dir,
        declarations_dir=bundle_root,
        repos=(RepoConfig(name=repo_name, path=tmp_path / repo_name, ignore=()),),
        state_gate=StateGateConfig(enabled=False, branches=()),
    )


# --- placement_directories: the repo-scoped types are excluded ---------------


def test_placement_directories_excludes_the_four_repo_scoped_types() -> None:
    """`placement_directories` narrows `declared_directories` to the types a
    plain directory-prefix check can still express -- Repository, File and
    Dependency -- dropping Package, App, TestSuite and AgentPlugin, which now
    nest under `repositories/<repo>/`."""
    schema_set = load_schemas(_ASSETS / "_schema")
    declared = declared_directories(schema_set)
    assert declared == {
        "AgentPlugin": "agent-plugins/",
        "App": "apps/",
        "Dependency": "dependencies/",
        "File": "repositories/",
        "Package": "packages/",
        "Repository": "repositories/",
        "TestSuite": "test-suites/",
    }

    narrowed = placement_directories(schema_set)

    assert narrowed == {
        "Dependency": "dependencies/",
        "File": "repositories/",
        "Repository": "repositories/",
    }
    assert not set(narrowed) & {"Package", "App", "TestSuite", "AgentPlugin"}


# --- dry_run=True (the default): nothing touches disk ------------------------


def test_dry_run_default_touches_nothing(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, "acme", "repo-a", ["widgets"])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, "repo-a", bundle_root=bundle_root)

    before_index = (bundle_root / "index.md").read_text(encoding="utf-8")
    before_log = (bundle_root / "log.md").read_text(encoding="utf-8")
    before_members = sorted(p.relative_to(bundle_root).as_posix() for p in bundle_root.rglob("*.md"))

    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        result = sync(bundle, config, reader, today=_TODAY, at=_AT)  # dry_run defaults to True

    assert result.written == ()
    assert result.deleted == ()
    assert not (bundle_root / "packages" / "widgets.md").exists()
    assert (bundle_root / "index.md").read_text(encoding="utf-8") == before_index
    assert (bundle_root / "log.md").read_text(encoding="utf-8") == before_log
    after_members = sorted(p.relative_to(bundle_root).as_posix() for p in bundle_root.rglob("*.md"))
    assert after_members == before_members


def test_explicit_dry_run_true_also_touches_nothing(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, "acme", "repo-a", ["widgets"])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, "repo-a", bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        sync(bundle, config, reader, today=_TODAY, at=_AT, dry_run=True)

    assert not (bundle_root / "packages" / "widgets.md").exists()


# --- dry_run=False: the full pipeline runs ------------------------------------


def test_sync_writes_pages_reconciles_index_and_appends_one_log_entry(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, "acme", "repo-a", ["widgets"])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, "repo-a", bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        result = sync(bundle, config, reader, today=_TODAY, at=_AT, dry_run=False)

    assert "repositories/repo-a/packages/widgets" in result.written
    assert (bundle_root / "repositories" / "repo-a" / "packages" / "widgets.md").exists()
    assert (bundle_root / "repositories" / "repo-a" / "packages" / "index.md").exists()
    index_text = (bundle_root / "repositories" / "repo-a" / "packages" / "index.md").read_text(encoding="utf-8")
    assert "widgets.md" in index_text

    log_text = (bundle_root / "log.md").read_text(encoding="utf-8")
    assert "created" in log_text.lower() or "written" in log_text.lower()


def test_exactly_one_log_entry_per_run_even_when_nothing_changed(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, "acme", "repo-a", ["widgets"])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, "repo-a", bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        sync(bundle, config, reader, today=_TODAY, at=_AT, dry_run=False)

    # Second run: idempotent at the entity-sync level (nothing to write, per
    # test_sync.py's own idempotency assertion) but sync() must still append
    # exactly one more dated bullet naming the (zero) counts.
    log_before = (bundle_root / "log.md").read_text(encoding="utf-8")
    bullets_before = log_before.count("\n- ")

    with open_reader(graph_dir=graph_dir) as reader:
        bundle2 = load_bundle(bundle_root)
        result2 = sync(bundle2, config, reader, today=_TODAY, at=_AT, dry_run=False)

    assert result2.written == ()
    assert result2.deleted == ()
    log_after = (bundle_root / "log.md").read_text(encoding="utf-8")
    bullets_after = log_after.count("\n- ")
    assert bullets_after == bullets_before + 1


# --- the deletion regression: a page whose entity disappeared gets deleted ---


def test_page_whose_entity_disappeared_from_the_graph_is_deleted(tmp_path: Path) -> None:
    """The exact bug the plan's draft `should_exist = set(resource_index(current).by_resource)`
    would have hidden: that expression indexes whatever is CURRENTLY ON DISK,
    which trivially includes the stale page itself, so nothing would ever be
    absent from `should_exist` and `prune_lane` would never find a deletion
    candidate. `sync()` must instead compute `should_exist` from
    `EntitySync.current_resources` -- the resources this run's graph walk
    actually named -- so a package removed from the graph is recognized as
    gone and its page is pruned."""
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, "acme", "repo-a", ["widgets", "gadgets"])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, "repo-a", bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        sync(bundle, config, reader, today=_TODAY, at=_AT, dry_run=False)

    assert (bundle_root / "repositories" / "repo-a" / "packages" / "widgets.md").exists()
    assert (bundle_root / "repositories" / "repo-a" / "packages" / "gadgets.md").exists()

    # "widgets" disappears from the graph -- e.g. the package was removed
    # from the repo. `upsert_records` is additive-only (see `_seed`'s own
    # docstring), so a fresh `graph_dir` is what makes a node actually
    # absent rather than merely un-reasserted. Its page's prose sections
    # are untouched (fresh from `new_page_text`), so the prose guard must
    # not decline it.
    graph_dir2 = tmp_path / "graph2"
    _seed(graph_dir2, "acme", "repo-a", ["gadgets"])
    config2 = _config(tmp_path, graph_dir2, "repo-a", bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir2) as reader:
        bundle2 = load_bundle(bundle_root)
        result2 = sync(bundle2, config2, reader, today=_TODAY, at=_AT, dry_run=False)

    assert not (bundle_root / "repositories" / "repo-a" / "packages" / "widgets.md").exists()
    assert (bundle_root / "repositories" / "repo-a" / "packages" / "gadgets.md").exists()
    assert "repositories/repo-a/packages/widgets" in result2.deleted
    assert result2.declined == ()

    # The index must be reconciled too: the dead entry pruned, the survivor
    # still listed.
    index_text = (bundle_root / "repositories" / "repo-a" / "packages" / "index.md").read_text(encoding="utf-8")
    assert "widgets.md" not in index_text
    assert "gadgets.md" in index_text

    log_text = (bundle_root / "log.md").read_text(encoding="utf-8")
    assert "deleted" in log_text.lower()


def test_hand_edited_page_declines_deletion_and_is_reported(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    _seed(graph_dir, "acme", "repo-a", ["widgets"])
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = _config(tmp_path, graph_dir, "repo-a", bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir) as reader:
        bundle = load_bundle(bundle_root)
        sync(bundle, config, reader, today=_TODAY, at=_AT, dry_run=False)

    page = bundle_root / "repositories" / "repo-a" / "packages" / "widgets.md"
    placeholder = "> TODO: what this package does, who uses it, and why it exists, in one paragraph."
    original = page.read_text(encoding="utf-8")
    assert placeholder in original
    page.write_text(original.replace(placeholder, "Hand-written, do not delete me."), encoding="utf-8")

    graph_dir2 = tmp_path / "graph2"
    _seed(graph_dir2, "acme", "repo-a", [])  # widgets gone from the graph
    config2 = _config(tmp_path, graph_dir2, "repo-a", bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir2) as reader:
        bundle2 = load_bundle(bundle_root)
        result2 = sync(bundle2, config2, reader, today=_TODAY, at=_AT, dry_run=False)

    assert page.exists()
    assert "Hand-written, do not delete me." in page.read_text(encoding="utf-8")
    assert result2.deleted == ()
    assert ("repositories/repo-a/packages/widgets", "prose-edited") in result2.declined


def test_a_package_disappearing_from_either_repo_only_prunes_that_repos_same_named_page(
    tmp_path: Path,
) -> None:
    graph_dir = tmp_path / "graph"
    store = open_store(graph_dir / "code.db", create=True)
    try:
        for org, repo in (("acme", "repo-a"), ("acme", "repo-b")):
            store.set_current_repo(f"repo:{org}/{repo}")
            nodes = [_repo_node(org, repo), _package_node(org, repo, "shared-name")]
            with store.transaction() as tx:
                tx.upsert_records(GraphRecords(nodes=tuple(nodes), edges=()))
        store.set_current_repo(None)
    finally:
        store.close()

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = Config(
        graph_dir=graph_dir,
        declarations_dir=bundle_root,
        repos=(
            RepoConfig(name="repo-a", path=tmp_path / "repo-a", ignore=()),
            RepoConfig(name="repo-b", path=tmp_path / "repo-b", ignore=()),
        ),
        state_gate=StateGateConfig(enabled=False, branches=()),
    )

    with open_reader(graph_dir=graph_dir) as reader:
        sync(load_bundle(bundle_root), config, reader, today=_TODAY, at=_AT, dry_run=False)

    repo_a_page = bundle_root / "repositories" / "repo-a" / "packages" / "shared-name.md"
    repo_b_page = bundle_root / "repositories" / "repo-b" / "packages" / "shared-name.md"
    assert repo_a_page.exists()
    assert repo_b_page.exists()

    # "shared-name" disappears from repo-a's graph only -- a fresh graph_dir
    # for repo-a, repo-b re-seeded unchanged (upsert_records is additive-only).
    graph_dir2 = tmp_path / "graph2"
    store2 = open_store(graph_dir2 / "code.db", create=True)
    try:
        store2.set_current_repo("repo:acme/repo-a")
        with store2.transaction() as tx:
            tx.upsert_records(GraphRecords(nodes=(_repo_node("acme", "repo-a"),), edges=()))
        store2.set_current_repo("repo:acme/repo-b")
        with store2.transaction() as tx:
            nodes_b = (_repo_node("acme", "repo-b"), _package_node("acme", "repo-b", "shared-name"))
            tx.upsert_records(GraphRecords(nodes=nodes_b, edges=()))
        store2.set_current_repo(None)
    finally:
        store2.close()

    config2 = replace(config, graph_dir=graph_dir2)
    with open_reader(graph_dir=graph_dir2) as reader:
        result2 = sync(load_bundle(bundle_root), config2, reader, today=_TODAY, at=_AT, dry_run=False)

    assert not repo_a_page.exists()
    assert repo_b_page.exists()
    assert "repositories/repo-a/packages/shared-name" in result2.deleted
    assert "repositories/repo-b/packages/shared-name" not in result2.deleted

    # Now "shared-name" disappears from repo-b's graph too -- a fresh
    # graph_dir again, repo-a re-seeded unchanged (still gone). This proves
    # the per-repo pruning loop maps *each* repo's own prefix on its own
    # iteration, not just repo-a's (which happens to be `config.repos[0]`):
    # without this second step a bug that hardcoded or mixed up which repo's
    # prefix the loop uses could pass the assertions above undetected.
    graph_dir3 = tmp_path / "graph3"
    store3 = open_store(graph_dir3 / "code.db", create=True)
    try:
        for org, repo in (("acme", "repo-a"), ("acme", "repo-b")):
            store3.set_current_repo(f"repo:{org}/{repo}")
            with store3.transaction() as tx:
                tx.upsert_records(GraphRecords(nodes=(_repo_node(org, repo),), edges=()))
        store3.set_current_repo(None)
    finally:
        store3.close()

    config3 = replace(config, graph_dir=graph_dir3)
    with open_reader(graph_dir=graph_dir3) as reader:
        result3 = sync(load_bundle(bundle_root), config3, reader, today=_TODAY, at=_AT, dry_run=False)

    assert not repo_a_page.exists()
    assert not repo_b_page.exists()
    assert "repositories/repo-b/packages/shared-name" in result3.deleted
    assert "repositories/repo-a/packages/shared-name" not in result3.deleted


# --- the root Repositories catalog ---------------------------------------


def _two_repo_bundle(tmp_path: Path) -> tuple[Path, Path, Config]:
    """Two repos in one graph, both configured, both with a described page."""
    graph_dir = tmp_path / "graph"
    store = open_store(graph_dir / "code.db", create=True)
    try:
        for org, repo, packages in (("acme", "repo-a", ["widgets"]), ("acme", "repo-b", ["gadgets"])):
            store.set_current_repo(f"repo:{org}/{repo}")
            nodes = [_repo_node(org, repo), *[_package_node(org, repo, name) for name in packages]]
            with store.transaction() as tx:
                tx.upsert_records(GraphRecords(nodes=tuple(nodes), edges=()))
        store.set_current_repo(None)
    finally:
        store.close()
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = Config(
        graph_dir=graph_dir,
        declarations_dir=bundle_root,
        repos=(
            RepoConfig(name="repo-a", path=tmp_path / "repo-a", ignore=()),
            RepoConfig(name="repo-b", path=tmp_path / "repo-b", ignore=()),
        ),
        state_gate=StateGateConfig(enabled=False, branches=()),
    )
    return graph_dir, bundle_root, config


def _sync(graph_dir: Path, bundle_root: Path, config: Config) -> SyncSummary:
    with open_reader(graph_dir=graph_dir) as reader:
        return sync(load_bundle(bundle_root), config, reader, today=_TODAY, at=_AT, dry_run=False)


def test_the_root_index_lists_every_repository(tmp_path: Path) -> None:
    """AC 1."""
    graph_dir, bundle_root, config = _two_repo_bundle(tmp_path)
    index = bundle_root / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + "\nHand-written prose nobody may touch.\n", encoding="utf-8")
    _sync(graph_dir, bundle_root, config)

    # Describe both repositories, the way a human would, then sync again.
    for name in ("repo-a", "repo-b"):
        page = bundle_root / "repositories" / f"{name}.md"
        page.write_text(
            page.read_text(encoding="utf-8").replace('description: ""', f'description: "the {name} platform"'),
            encoding="utf-8",
        )
    _sync(graph_dir, bundle_root, config)

    body = index.read_text(encoding="utf-8")
    assert "Hand-written prose nobody may touch." in body
    assert "## Repositories" in body
    assert "- [repo-a](/repositories/repo-a.md) — the repo-a platform" in body
    assert "- [repo-b](/repositories/repo-b.md) — the repo-b platform" in body


def test_a_second_sync_plans_no_root_catalog_change(tmp_path: Path) -> None:
    """AC 3: idempotence surfaces as an empty plan, not as an unchanged write."""
    graph_dir, bundle_root, config = _two_repo_bundle(tmp_path)
    _sync(graph_dir, bundle_root, config)
    before = (bundle_root / "index.md").read_bytes()
    result = _sync(graph_dir, bundle_root, config)

    assert result.catalog == ()
    assert (bundle_root / "index.md").read_bytes() == before

    bundle = load_bundle(bundle_root)
    section_set = load_sections(bundle_root / "_sections")
    plan = plan_regenerate(
        bundle,
        section_set,
        {},
        index_renders={"": Render(sections={"Repositories": render_repositories(repository_entries(bundle))})},
    )
    assert plan.is_empty


def test_a_repository_removed_from_the_graph_loses_its_bullet(tmp_path: Path) -> None:
    """AC 4. `upsert_records` is additive, so "a repo disappeared" needs a
    fresh graph_dir for the second seed."""
    graph_dir, bundle_root, config = _two_repo_bundle(tmp_path)
    _sync(graph_dir, bundle_root, config)
    assert "- [repo-b](/repositories/repo-b.md)" in (bundle_root / "index.md").read_text(encoding="utf-8")

    second_graph = tmp_path / "graph2"
    _seed(second_graph, "acme", "repo-a", ["widgets"])
    narrowed = replace(
        config, graph_dir=second_graph, repos=(RepoConfig(name="repo-a", path=tmp_path / "repo-a", ignore=()),)
    )
    _sync(second_graph, bundle_root, narrowed)

    body = (bundle_root / "index.md").read_text(encoding="utf-8")
    assert "- [repo-a](/repositories/repo-a.md)" in body
    assert "repo-b" not in body
    # Reconciliation added the lane as a subdirectory entry, not a second
    # repository bullet inside `## Repositories`.
    repositories_block = body.split("## Repositories", 1)[1].split("\n## ", 1)[0]
    assert "/repositories/index.md" not in repositories_block
    assert "(repositories)" not in repositories_block


def test_the_root_is_always_reconciled(tmp_path: Path) -> None:
    """`update_index(directories=[*touched_lanes, ""])`: the lane list at root
    is reconciliation's half of the catalog, and it runs even on a sync that
    touched no lane.

    The heading level okf_io.index chooses for a newly-created section
    follows the root index's *first* existing heading -- here the `# bundle`
    H1 the scaffold writes -- not the level of a sibling section a different
    writer created, so this checks for the "Subdirectories" heading without
    pinning its level.
    """
    graph_dir, bundle_root, config = _two_repo_bundle(tmp_path)
    _sync(graph_dir, bundle_root, config)
    body = (bundle_root / "index.md").read_text(encoding="utf-8")
    heading = next(line for line in body.splitlines() if line.lstrip("#").strip() == "Subdirectories")
    assert "repositories" in body.split(heading, 1)[1]


def test_the_log_line_names_the_catalog(tmp_path: Path) -> None:
    graph_dir, bundle_root, config = _two_repo_bundle(tmp_path)
    _sync(graph_dir, bundle_root, config)
    assert "catalog" in (bundle_root / "log.md").read_text(encoding="utf-8")

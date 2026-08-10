"""Acceptance-level proof for the entity-lanes design spec's "done when"
list (`01-design-spec.md`, "Testing / 'done when', made concrete"), driven
through the real top-level entry point, `code_wiki_okf.entities.lanes.sync`
-- not the lower-level `entities.sync.sync_entities` the per-module unit
tests already cover in `test_sync.py` and `test_lanes.py`.

This file is deliberately one continuous narrative against one realistic
two-repo fixture graph (`acme/repo-a` with packages `widgets` + `sprockets`,
`acme/repo-b` with package `gadgets`), run through a sequence of `sync()`
calls with hand-edits and graph mutations interleaved -- proving the four
acceptance criteria work *together*, end to end, the way a real bundle would
experience them across repeated runs, rather than as isolated unit cases.

Fixture graphs are seeded the same way `test_sync.py` / `test_lanes.py` do:
directly through `code_graph_io.testing.open_store` (a writable `GraphStore`
on an arbitrary db path) plus `code_parser.projections.graph.GraphNode` /
`GraphRecords`. `code_graph_io.testing` carries no `build_records` helper.
`upsert_records` is additive-only -- it never removes a node a prior call
inserted -- so simulating "a package disappeared from the graph" needs a
**fresh** `graph_dir` for that later seed, not a second call against the
same one (see `_seed`'s own docstring, and `test_lanes.py`'s identical
pattern).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from code_graph_io import open_reader
from code_graph_io.testing import open_store
from code_parser.projections.graph import GraphNode, GraphRecords
from code_wiki_okf.config import Config, RepoConfig, StateGateConfig
from code_wiki_okf.entities.lanes import sync
from code_wiki_okf.init import install_bundle
from okf_io import load_bundle

_TODAY = date(2026, 1, 1)
_AT = datetime(2026, 1, 1, tzinfo=UTC)

_PURPOSE_PLACEHOLDER = "> TODO: <One paragraph: what this package does, who uses it, why it exists.>"


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


def _seed(
    graph_dir: Path, org: str, repo: str, packages: Sequence[str], *, versions: dict[str, str] | None = None
) -> None:
    """Write `graph_dir/code.db` with exactly the given packages for one
    repo (plus that repo's own Repository node). Additive only within one
    `graph_dir` -- see module docstring -- so a caller wanting a package to
    *disappear* between two `sync()` calls must pass a fresh `graph_dir`,
    never re-seed this one."""
    versions = versions or {}
    store = open_store(graph_dir / "code.db", create=True)
    try:
        store.set_current_repo(f"repo:{org}/{repo}")
        nodes: list[GraphNode] = [_repo_node(org, repo)]
        nodes += [_package_node(org, repo, name, version=versions.get(name, "0.1.0")) for name in packages]
        with store.transaction() as tx:
            tx.upsert_records(GraphRecords(nodes=tuple(nodes), edges=()))
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


def test_entity_lanes_done_when(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)

    # --- Generation 1: a realistic two-repo graph -----------------------
    # repo-a carries two packages (one gets hand-edited prose, the other
    # gets moved); repo-b carries one (used for the untouched-prose
    # deletion criterion).
    graph_dir1 = tmp_path / "graph1"
    _seed(graph_dir1, "acme", "repo-a", ["widgets", "sprockets"])
    _seed(graph_dir1, "acme", "repo-b", ["gadgets"])
    config1 = _config(tmp_path, graph_dir1, ["repo-a", "repo-b"], bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir1) as reader:
        bundle = load_bundle(bundle_root)
        result1 = sync(bundle, config1, reader, today=_TODAY, at=_AT, dry_run=False)

    expected_written = {
        "packages/widgets",
        "packages/sprockets",
        "packages/gadgets",
        "repositories/repo-a",
        "repositories/repo-b",
    }
    assert expected_written <= set(result1.written)
    assert result1.deleted == ()
    assert result1.declined == ()
    widgets_page = bundle_root / "packages" / "widgets.md"
    sprockets_page = bundle_root / "packages" / "sprockets.md"
    gadgets_page = bundle_root / "packages" / "gadgets.md"
    assert widgets_page.exists()
    assert sprockets_page.exists()
    assert gadgets_page.exists()
    packages_index = (bundle_root / "packages" / "index.md").read_text(encoding="utf-8")
    assert "widgets.md" in packages_index
    assert "sprockets.md" in packages_index
    assert "gadgets.md" in packages_index

    # --- Criterion 1: two consecutive sync() calls -- the second creates
    # and deletes nothing. -------------------------------------------------
    before_members = sorted(p.relative_to(bundle_root).as_posix() for p in bundle_root.rglob("*.md"))
    before_index = packages_index

    with open_reader(graph_dir=graph_dir1) as reader:
        bundle2 = load_bundle(bundle_root)
        result2 = sync(bundle2, config1, reader, today=_TODAY, at=_AT, dry_run=False)

    assert result2.written == ()
    assert result2.deleted == ()
    assert result2.declined == ()
    after_members = sorted(p.relative_to(bundle_root).as_posix() for p in bundle_root.rglob("*.md"))
    assert after_members == before_members
    assert (bundle_root / "packages" / "index.md").read_text(encoding="utf-8") == before_index

    # --- Criterion 2: a hand-edited prose section on a Package page
    # survives a third sync() call verbatim. --------------------------------
    original_widgets_text = widgets_page.read_text(encoding="utf-8")
    assert _PURPOSE_PLACEHOLDER in original_widgets_text
    hand_edit = "Widgets, hand-built for the acme storefront. Do not regenerate."
    edited_widgets_text = original_widgets_text.replace(_PURPOSE_PLACEHOLDER, hand_edit)
    widgets_page.write_text(edited_widgets_text, encoding="utf-8")

    with open_reader(graph_dir=graph_dir1) as reader:
        bundle3 = load_bundle(bundle_root)
        result3 = sync(bundle3, config1, reader, today=_TODAY, at=_AT, dry_run=False)

    final_widgets_text = widgets_page.read_text(encoding="utf-8")
    assert hand_edit in final_widgets_text
    assert "packages/widgets" not in result3.written
    assert result3.deleted == ()
    assert result3.declined == ()

    # --- Criterion 3: a page moved to a different path within its lane
    # (same resource:) is found and updated in place by the next sync(),
    # not duplicated. --------------------------------------------------------
    moved_page = bundle_root / "packages" / "moved-sprockets.md"
    moved_page.write_text(sprockets_page.read_text(encoding="utf-8"), encoding="utf-8")
    sprockets_page.unlink()

    # Bump sprockets' version in the graph too (same graph_dir -- an
    # upsert, not a removal, so additive-only semantics don't apply): this
    # also proves the moved page gets UPDATED at its new location, not left
    # stale.
    _seed(graph_dir1, "acme", "repo-a", ["widgets", "sprockets"], versions={"widgets": "0.1.0", "sprockets": "0.2.0"})

    with open_reader(graph_dir=graph_dir1) as reader:
        bundle4 = load_bundle(bundle_root)
        result4 = sync(bundle4, config1, reader, today=_TODAY, at=_AT, dry_run=False)

    assert not sprockets_page.exists()
    assert moved_page.exists()
    assert "packages/moved-sprockets" in result4.written
    assert result4.deleted == ()
    assert result4.declined == ()

    bundle_after_move = load_bundle(bundle_root)
    assert "packages/sprockets" not in bundle_after_move.concepts
    moved_doc = bundle_after_move.concept("packages/moved-sprockets")
    assert moved_doc is not None
    assert moved_doc.fm_raw.get("version") == "0.2.0"
    assert moved_doc.fm.resource == "pkg:acme/repo-a/sprockets"
    # The hand-edited widgets prose from criterion 2 is still untouched by
    # this unrelated sync() run -- the narrative's edits don't trample each
    # other.
    assert hand_edit in widgets_page.read_text(encoding="utf-8")

    # --- Criterion 4a: removing a package from the graph then syncing
    # deletes its untouched-prose page. -------------------------------------
    # gadgets' prose was never hand-edited, so it is the untouched-prose
    # half of the deletion criterion. `upsert_records` is additive-only, so
    # a genuinely *absent* node needs a fresh graph_dir: repo-b now has no
    # packages, repo-a keeps widgets (hand-edited) and sprockets (moved,
    # bumped).
    graph_dir2 = tmp_path / "graph2"
    _seed(graph_dir2, "acme", "repo-a", ["widgets", "sprockets"], versions={"sprockets": "0.2.0"})
    _seed(graph_dir2, "acme", "repo-b", [])  # gadgets gone
    config2 = _config(tmp_path, graph_dir2, ["repo-a", "repo-b"], bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir2) as reader:
        bundle5 = load_bundle(bundle_root)
        result5 = sync(bundle5, config2, reader, today=_TODAY, at=_AT, dry_run=False)

    assert not gadgets_page.exists()
    assert "packages/gadgets" in result5.deleted
    assert result5.declined == ()
    assert widgets_page.exists()
    assert moved_page.exists()
    packages_index_after_delete = (bundle_root / "packages" / "index.md").read_text(encoding="utf-8")
    assert "gadgets.md" not in packages_index_after_delete
    assert "widgets.md" in packages_index_after_delete
    assert "moved-sprockets.md" in packages_index_after_delete

    # --- Criterion 4b: the same scenario, but with hand-edited prose,
    # declines the deletion. -------------------------------------------------
    # widgets' prose was hand-edited back in criterion 2 and never
    # reverted. Now remove widgets from the graph too (another fresh
    # graph_dir, for the same additive-only reason) and confirm the guard
    # refuses to delete it, reporting why instead.
    graph_dir3 = tmp_path / "graph3"
    _seed(graph_dir3, "acme", "repo-a", ["sprockets"], versions={"sprockets": "0.2.0"})  # widgets gone too
    _seed(graph_dir3, "acme", "repo-b", [])
    config3 = _config(tmp_path, graph_dir3, ["repo-a", "repo-b"], bundle_root=bundle_root)

    with open_reader(graph_dir=graph_dir3) as reader:
        bundle6 = load_bundle(bundle_root)
        result6 = sync(bundle6, config3, reader, today=_TODAY, at=_AT, dry_run=False)

    assert widgets_page.exists()
    assert hand_edit in widgets_page.read_text(encoding="utf-8")
    assert "packages/widgets" not in result6.deleted
    assert ("packages/widgets", "prose-edited") in result6.declined

    log_text = (bundle_root / "log.md").read_text(encoding="utf-8")
    # Two bullets from `install_bundle` (the scaffold's, then the install's)
    # plus six sync() calls, each of which -- including the two idempotent /
    # declined-only ones -- appends exactly one more (design spec: "One
    # `okf_io.append_log_entry` call per `sync_entities` run").
    assert log_text.count("\n- ") == 8

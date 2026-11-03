"""A reference clone under `repositories/` is invisible to `gw query` retrieval.

The clone is a foreign checkout: its markdown carries someone else's frontmatter and prose. The
clone ignore glob and prune keep it out of every bundle load, so a query whose only matching text
lives in the clone must return nothing from it -- through the read session, the in-memory bundle
path, and the lexical-only and hybrid rankings alike.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import FakeEmbedder  # type: ignore[import-not-found]
from gw_kernel.workspace.bundle import load_workspace_bundle
from gw_wiki.query import commands as q

NEEDLE = "zyxquillfoo"
CLONE_DIR = Path("repositories") / "upstream" / "references" / "git"


def _plant_clone(layout) -> list[Path]:
    clone = layout.bundle_dir / CLONE_DIR
    planted = [
        clone / "docs" / "guide.md",
        clone / "README.md",
        clone / "nested" / "deep" / "note.md",
    ]
    for path in planted:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntitle: Clone {path.stem}\n---\n\n{NEEDLE} {NEEDLE} {NEEDLE}\n", encoding="utf-8")
    return planted


def _plant_real_page(layout) -> None:
    (layout.bundle_dir / "s" / "real.md").write_text(
        f"---\ntitle: Real\n---\n\nthe genuine page mentions {NEEDLE} once\n", encoding="utf-8"
    )


@pytest.fixture
def layout(make_workspace):
    return make_workspace("synthetic", seed=3)


def _paths(brief: q.QueryBrief) -> list[str]:
    return [page.path for page in brief.top_pages]


def test_the_loaded_bundle_has_no_clone_member(layout):
    _plant_clone(layout)
    bundle = load_workspace_bundle(layout)
    assert not [cid for cid in bundle.concepts if cid.startswith("repositories/")]


@pytest.mark.parametrize("embedder", [None, FakeEmbedder()], ids=["lexical", "hybrid"])
def test_a_query_matching_only_the_clone_returns_no_clone_page(layout, embedder):
    _plant_clone(layout)
    brief = q.plan_query_brief(NEEDLE, layout, embedder=embedder, top_k=5)
    assert not [p for p in _paths(brief) if "repositories" in p or "references/git" in p]


@pytest.mark.parametrize("embedder", [None, FakeEmbedder()], ids=["lexical", "hybrid"])
def test_a_clone_page_never_displaces_the_real_page(layout, embedder):
    _plant_real_page(layout)
    _plant_clone(layout)
    brief = q.plan_query_brief(NEEDLE, layout, embedder=embedder, top_k=5)
    paths = _paths(brief)
    assert "s/real" in paths
    assert not [p for p in paths if "references/git" in p]


def test_the_in_memory_bundle_path_also_hides_the_clone(layout):
    _plant_real_page(layout)
    _plant_clone(layout)
    bundle = load_workspace_bundle(layout)
    brief = q.plan_query_brief(NEEDLE, layout, bundle=bundle, embedder=None, top_k=5)
    assert "s/real" in _paths(brief)
    assert not [p for p in _paths(brief) if "references/git" in p]


def test_the_embedding_index_never_stores_a_clone_page(layout):
    _plant_clone(layout)
    embedder = FakeEmbedder()
    q.plan_query_brief(NEEDLE, layout, embedder=embedder, top_k=5)
    bundle = load_workspace_bundle(layout)
    q.build_index(bundle, layout.cache_dir, embedder=embedder)
    with sqlite3.connect(str(layout.cache_dir / "search" / "search.db")) as conn:
        stored = [row[0] for row in conn.execute("SELECT path FROM pages")]
    assert stored
    assert not [p for p in stored if "references/git" in p]

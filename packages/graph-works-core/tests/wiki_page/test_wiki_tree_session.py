"""`wiki_tree` over a read session: one-file parse, row titles, exact-id parity."""

from __future__ import annotations

import pytest
from graph_works_core.read_session import bundle_backend, open_read_session
from graph_works_core.wiki_page import commands as wiki_commands
from graph_works_core.wiki_page import run_wiki_tree, wiki_tree
from graph_works_core.wiki_page.commands import TreePage
from okf_ext import readindex
from okf_ext.readindex import sync
from read_model_helpers import age_bundle, new_layout, oracle_wiki_tree, populate_generated, write, write_bytes


@pytest.fixture
def layout(tmp_path):
    layout = new_layout(tmp_path)
    populate_generated(layout)
    age_bundle(layout)
    return layout


@pytest.fixture
def counters(monkeypatch):
    seen = {"core_parse": [], "index_parse": [], "graphs": 0, "loads": 0, "reconciles": 0}
    real_core, real_sync = wiki_commands.read_member, sync.read_member
    monkeypatch.setattr(
        wiki_commands, "read_member", lambda root, mid: seen["core_parse"].append(mid) or real_core(root, mid)
    )
    monkeypatch.setattr(sync, "read_member", lambda root, mid: seen["index_parse"].append(mid) or real_sync(root, mid))
    real_graph, real_load = bundle_backend.build_link_graph, bundle_backend.load_bundle_at

    def graph(*a, **k):
        seen["graphs"] += 1
        return real_graph(*a, **k)

    def load(*a, **k):
        seen["loads"] += 1
        return real_load(*a, **k)

    monkeypatch.setattr(bundle_backend, "build_link_graph", graph)
    monkeypatch.setattr(bundle_backend, "load_bundle_at", load)
    real_reconcile = readindex.reconcile

    def reconcile(index):
        seen["reconciles"] += 1
        return real_reconcile(index)

    monkeypatch.setattr(readindex, "reconcile", reconcile)
    return seen


def test_tree_matches_the_full_load(layout) -> None:
    tree = run_wiki_tree(layout)
    assert tree == oracle_wiki_tree(layout)
    docs = tree.sections[0].children[0]
    ids = [page.id for page in docs.pages]
    assert ids == ["docs/p", "docs/yaml", "docs/café", "work/feature-a/references/01-design"]
    assert docs.pages[0] == TreePage(id="docs/p", title="P", type="Explanation")


def test_warm_tree_parses_only_the_root_index(layout, counters) -> None:
    age_bundle(layout)
    run_wiki_tree(layout)
    counters["core_parse"].clear()
    counters["index_parse"].clear()
    counters["graphs"] = counters["loads"] = 0
    run_wiki_tree(layout)
    assert counters["core_parse"] == ["index.md"]
    assert counters["index_parse"] == []
    assert (counters["graphs"], counters["loads"]) == (0, 0)


def test_unrelated_pages_do_not_change_the_cost(layout, counters) -> None:
    run_wiki_tree(layout)
    for n in range(50):
        write(layout, f"docs/extra/e{n}.md", f"---\ntitle: E{n}\n---\n")
    age_bundle(layout)
    run_wiki_tree(layout)
    counters["core_parse"].clear()
    counters["index_parse"].clear()
    counters["graphs"] = counters["loads"] = 0
    run_wiki_tree(layout)
    assert counters["core_parse"] == ["index.md"]
    assert counters["index_parse"] == []
    assert (counters["graphs"], counters["loads"]) == (0, 0)


@pytest.mark.parametrize("damage", ["missing", "undecodable"])
def test_missing_or_undecodable_root_index_is_empty(layout, damage) -> None:
    path = layout.bundle_dir / "index.md"
    if damage == "missing":
        path.unlink()
    else:
        write_bytes(layout, "index.md", b"# Index\n\xff\xfe\n## Concepts\n")
    assert run_wiki_tree(layout).sections == ()
    assert oracle_wiki_tree(layout).sections == ()


def test_root_index_deleted_after_reconcile_is_empty(layout) -> None:
    with open_read_session(layout) as session:
        (layout.bundle_dir / "index.md").unlink()
        assert wiki_tree(session, layout).sections == ()


def test_title_falls_back_to_label_then_id(layout) -> None:
    write(layout, "index.md", "# I\n\n## S\n\n- [Label](/docs/untitled.md)\n- [](/docs/blank.md)\n")
    write(layout, "docs/untitled.md", "---\ndescription: d\n---\n")
    write(layout, "docs/blank.md", "---\ntitle: '  '\n---\n")
    tree = run_wiki_tree(layout)
    assert tree == oracle_wiki_tree(layout)
    assert [(p.title, p.type) for p in tree.sections[0].pages] == [("Label", None), ("docs/blank", None)]


def test_one_session_serves_two_trees(layout, counters) -> None:
    with open_read_session(layout) as session:
        first = wiki_tree(session, layout)
        second = wiki_tree(session, layout)
    assert first == second == oracle_wiki_tree(layout)
    assert counters["reconciles"] == 1

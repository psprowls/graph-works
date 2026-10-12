"""`wiki_tree` over a read session: warm-zero parses, row titles, exact-id parity."""

from __future__ import annotations

import pytest
from gw_kernel.read_session import BundleSession, bundle_backend, materialize, open_read_session
from gw_wiki.wiki_page import run_wiki_tree, wiki_tree
from gw_wiki.wiki_page.commands import TreePage
from okf_ext import readindex
from okf_io.document import Document
from read_model_helpers import age_bundle, new_layout, oracle_wiki_tree, populate_generated, write, write_bytes


@pytest.fixture
def layout(tmp_path):
    layout = new_layout(tmp_path)
    populate_generated(layout)
    age_bundle(layout)
    return layout


@pytest.fixture
def counters(monkeypatch):
    seen = {"parses": [], "graphs": 0, "loads": 0, "reconciles": 0}
    real_parse = Document.parse

    def parse(cls, text, *, path=None):
        seen["parses"].append(path)
        return real_parse(text, path=path)

    monkeypatch.setattr(Document, "parse", classmethod(parse))
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

    def reconcile(index, **kwargs):
        seen["reconciles"] += 1
        return real_reconcile(index, **kwargs)

    monkeypatch.setattr(readindex, "reconcile", reconcile)
    return seen


def _reset(seen) -> None:
    seen["parses"].clear()
    seen["graphs"] = seen["loads"] = seen["reconciles"] = 0


def test_tree_matches_the_full_load(layout) -> None:
    tree = run_wiki_tree(layout)
    assert tree == oracle_wiki_tree(layout)
    docs = tree.sections[0].children[0]
    ids = [page.id for page in docs.pages]
    assert ids == ["docs/p", "docs/yaml", "docs/café", "work/feature-a/references/01-design"]
    assert docs.pages[0] == TreePage(id="docs/p", title="P", type="Explanation")


def test_warm_tree_parses_no_files_and_builds_no_graph(layout, counters) -> None:
    run_wiki_tree(layout)  # warm-up: builds the index
    _reset(counters)
    with open_read_session(layout) as session:
        assert session.backend == "index"
        tree = wiki_tree(session, layout)
    assert counters["parses"] == []
    assert (counters["graphs"], counters["loads"]) == (0, 0)
    assert tree == oracle_wiki_tree(layout)


def test_unrelated_pages_do_not_change_the_cost(layout, counters) -> None:
    run_wiki_tree(layout)
    for n in range(50):
        write(layout, f"docs/extra/e{n}.md", f"---\ntitle: E{n}\n---\n")
    age_bundle(layout)
    run_wiki_tree(layout)  # warm again after the additions
    _reset(counters)
    run_wiki_tree(layout)
    assert counters["parses"] == []
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


def test_warm_session_serves_two_trees_without_parsing(layout, counters) -> None:
    run_wiki_tree(layout)
    _reset(counters)
    with open_read_session(layout) as session:
        first = wiki_tree(session, layout)
        second = run_wiki_tree(layout, session=session)
    assert first == second
    assert counters["parses"] == []
    assert counters["reconciles"] == 1


ROOT_EDIT = "# Index\n\n## Edited section\n\n- [P](/docs/p.md)\n"


@pytest.mark.parametrize("edited", ["index.md", "docs/p.md"])
def test_one_edit_before_open_parses_only_the_edited_file(layout, counters, edited) -> None:
    run_wiki_tree(layout)
    if edited == "index.md":
        write(layout, edited, ROOT_EDIT)
    else:
        write(layout, edited, "---\ntitle: Retitled\ntype: Explanation\n---\nBody.\n")
    age_bundle(layout)
    _reset(counters)
    tree = run_wiki_tree(layout)
    assert counters["parses"] == [layout.bundle_dir / edited]
    assert (counters["graphs"], counters["loads"]) == (0, 0)
    assert tree == oracle_wiki_tree(layout)
    if edited == "index.md":
        assert [s.heading for s in tree.sections] == ["Edited section"]
    else:
        pages = [p for s in tree.sections for n in (s, *s.children) for p in n.pages]
        assert TreePage(id="docs/p", title="Retitled", type="Explanation") in pages


def test_root_edit_after_reconcile_parses_the_new_bytes_once(layout, counters) -> None:
    run_wiki_tree(layout)
    with open_read_session(layout) as session:
        _reset(counters)
        write(layout, "index.md", ROOT_EDIT)
        tree = wiki_tree(session, layout)
    assert len(counters["parses"]) == 1  # the content hash mismatch
    assert [s.heading for s in tree.sections] == ["Edited section"]


def test_materialized_session_serves_warm_tree_after_source_closes(layout, counters) -> None:
    with open_read_session(layout) as source:
        assert source.backend == "index"
        snapshot = materialize(source)
        expected = wiki_tree(source, layout)
    _reset(counters)
    assert run_wiki_tree(layout, session=snapshot) == expected
    assert counters["parses"] == []
    assert counters["reconciles"] == 0


def test_bundle_session_reuses_its_loaded_root(layout, counters) -> None:
    session = BundleSession(layout.bundle_dir, fallback=None)
    _reset(counters)
    first = wiki_tree(session, layout)
    assert counters["parses"].count(layout.bundle_dir / "index.md") == 1  # the full load's own parse
    _reset(counters)
    second = wiki_tree(session, layout)
    assert counters["parses"] == []
    assert first == second == oracle_wiki_tree(layout)


TRICKY_INDEX = """# Index

## Docs

Prose with a [prose link](/docs/p.md) is not an entry.

- [P](/docs/p.md "titled")
- [Yaml](</docs/yaml.md>) and [P again](/docs/p.md)
- ![img](/docs/p.md) image-led
  - [Nested](/docs/yaml.md)
- [P](/docs/p.md)

#### Deep

- [Deep yaml](/docs/yaml.md)

> ## Quoted
>
> - [Quoted](/docs/p.md)

```
## Fenced
- [Fenced](/docs/p.md)
```

### Sub

- [Sub p](/docs/p.md)

# Subdirectories

## After

- [After](/docs/p.md)
"""


def test_outline_semantics_match_the_full_load(layout, counters) -> None:
    write(layout, "index.md", TRICKY_INDEX)
    age_bundle(layout)
    run_wiki_tree(layout)
    _reset(counters)
    tree = run_wiki_tree(layout)
    assert counters["parses"] == []
    assert tree == oracle_wiki_tree(layout)
    assert [s.heading for s in tree.sections] == ["Docs"]
    assert [c.heading for c in tree.sections[0].children] == ["Sub"]

"""`run_code_graph_neighborhood`: depends-on neighbours in both directions, over a seeded graph."""

from __future__ import annotations

import json
from datetime import date

import pytest
from code_graph_io.testing import raw_conn
from graph_works_core import apply_init, plan_init
from graph_works_core.code_read import run_code_graph_neighborhood
from graph_works_core.graph.commands import graph_target

TODAY = date(2026, 9, 19)
REPO = "repo:acme/demo"
URI_A, URI_B, URI_C = "pkg:acme/demo/a", "pkg:acme/demo/b", "pkg:acme/demo/c"
URI_DEP = "dependency:acme/demo/pypi/requests"
FILE_X, FILE_Y, FILE_Z = "file:acme/demo/src/x.py", "file:acme/demo/src/y.py", "file:acme/demo/src/z.py"
PAGE_A, PAGE_B = "code-graph/demo/entities/packages/a", "code-graph/demo/entities/packages/b"
PAGE_NO_RESOURCE = "code-graph/demo/entities/packages/bare"
PAGE_FILE_Y = "code-graph/demo/file-system/src/y.py"
PAGE_GHOST = "code-graph/demo/entities/packages/ghost"

_NODES = (
    (1, "package", "a", "packages/a/pyproject.toml", URI_A),
    (2, "package", "b", "packages/b/pyproject.toml", URI_B),
    (3, "package", "c", "packages/c/pyproject.toml", URI_C),
    (4, "dependency", "requests", "dependency:acme/demo:pypi:requests", URI_DEP),
    (5, "file", "x.py", "src/x.py", FILE_X),
    (6, "file", "y.py", "src/y.py", FILE_Y),
    (7, "file", "z.py", "src/z.py", FILE_Z),
)
# a -> b -> c, b uses requests; x imports y imports z.
_EDGES = (
    (1, 2, "depends_on_package"),
    (2, 3, "depends_on_package"),
    (2, 4, "used_by"),
    (5, 6, "imports"),
    (6, 7, "imports"),
)


def _page(layout, page_id, resource=None) -> None:
    path = layout.bundle_dir / f"{page_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = f"type: Package\ntitle: {page_id.rsplit('/', 1)[-1]}\ndescription: d\n"
    if resource:
        fm += f"resource: {resource}\n"
    path.write_text(f"---\n{fm}---\n\nx\n", encoding="utf-8", newline="")


def _seed(layout) -> None:
    graph_dir = graph_target(layout).graph_dir
    graph_dir.mkdir(parents=True, exist_ok=True)
    conn = raw_conn(graph_dir / "code.db", create=True)
    try:
        with conn:
            for node_id, kind, name, path, uri in _NODES:
                conn.execute(
                    "INSERT INTO nodes (id, kind, name, path, line, attrs_json, uri, repo) "
                    "VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
                    (node_id, kind, name, path, json.dumps({"language": "python"}), uri, REPO),
                )
            conn.executemany("INSERT INTO edges (src, dst, kind) VALUES (?, ?, ?)", _EDGES)
    finally:
        conn.close()


@pytest.fixture
def layout_without_graph(tmp_path):
    built = apply_init(plan_init(tmp_path / "repo" / ".works", today=TODAY, topic="T")).layout
    _page(built, PAGE_A, URI_A)
    return built


@pytest.fixture
def graph_layout(layout_without_graph):
    layout = layout_without_graph
    _page(layout, PAGE_B, URI_B)
    _page(layout, PAGE_NO_RESOURCE)
    _page(layout, PAGE_FILE_Y, FILE_Y)
    _page(layout, PAGE_GHOST, "pkg:acme/demo/ghost")
    _seed(layout)
    return layout


def test_depth_one_from_b_sees_both_directions(graph_layout) -> None:
    result = run_code_graph_neighborhood(graph_layout, PAGE_B, 1)
    assert result.refusal is None and result.uri == URI_B
    assert {n.uri for n in result.nodes} == {URI_A, URI_B, URI_C, URI_DEP}
    assert {(e.source, e.target) for e in result.edges} == {(URI_A, URI_B), (URI_B, URI_C), (URI_B, URI_DEP)}
    by_uri = {n.uri: n for n in result.nodes}
    assert by_uri[URI_C].page_id is None
    assert by_uri[URI_A].page_id == PAGE_A and by_uri[URI_A].depth == 1 and by_uri[URI_B].depth == 0
    assert not result.truncated


def test_depth_one_from_a_does_not_reach_c(graph_layout) -> None:
    assert URI_C not in {n.uri for n in run_code_graph_neighborhood(graph_layout, PAGE_A, 1).nodes}


def test_depth_two_from_a_reaches_c(graph_layout) -> None:
    result = run_code_graph_neighborhood(graph_layout, PAGE_A, 2)
    assert {n.uri: n.depth for n in result.nodes}[URI_C] == 2


def test_file_neighbourhood_follows_imports_both_ways(graph_layout) -> None:
    result = run_code_graph_neighborhood(graph_layout, PAGE_FILE_Y, 1)
    assert {n.uri for n in result.nodes} == {FILE_X, FILE_Y, FILE_Z}
    assert {(e.source, e.target) for e in result.edges} == {(FILE_X, FILE_Y), (FILE_Y, FILE_Z)}


@pytest.mark.parametrize(
    ("page", "kind"), [("nope/x", "unknown-page"), (PAGE_NO_RESOURCE, "no-resource"), (PAGE_GHOST, "not-in-graph")]
)
def test_refusals(graph_layout, page, kind) -> None:
    assert run_code_graph_neighborhood(graph_layout, page, 1).refusal == kind


def test_no_graph(layout_without_graph) -> None:
    assert run_code_graph_neighborhood(layout_without_graph, PAGE_A, 1).refusal == "no-graph"


@pytest.mark.parametrize("depth", [0, 4])
def test_depth_out_of_range(graph_layout, depth) -> None:
    with pytest.raises(ValueError):
        run_code_graph_neighborhood(graph_layout, PAGE_A, depth)


def test_cap_truncates_and_keeps_edges_between_kept_nodes(graph_layout, monkeypatch) -> None:
    monkeypatch.setattr("graph_works_core.code_read.neighborhood._CAP", 2)
    result = run_code_graph_neighborhood(graph_layout, PAGE_B, 1)
    kept = {n.uri for n in result.nodes}
    assert result.truncated and len(kept) == 2
    assert all(e.source in kept and e.target in kept for e in result.edges)


def test_same_named_files_in_another_repository_do_not_leak_edges(graph_layout) -> None:
    other = "repo:acme/other"
    conn = raw_conn(graph_target(graph_layout).graph_dir / "code.db")
    try:
        with conn:
            for node_id, name, path, uri in (
                (20, "x.py", "src/x.py", "file:acme/other/src/x.py"),
                (21, "y.py", "src/y.py", "file:acme/other/src/y.py"),
            ):
                conn.execute(
                    "INSERT INTO nodes (id, kind, name, path, line, attrs_json, uri, repo) "
                    "VALUES (?, 'file', ?, ?, NULL, '{}', ?, ?)",
                    (node_id, name, path, uri, other),
                )
            conn.execute("INSERT INTO edges (src, dst, kind) VALUES (20, 21, 'imports')")
    finally:
        conn.close()
    # demo has src/x.py and src/y.py with no import between them (only `other` has x -> y).
    conn = raw_conn(graph_target(graph_layout).graph_dir / "code.db")
    try:
        with conn:
            conn.execute("DELETE FROM edges WHERE src = 5 AND dst = 6")
    finally:
        conn.close()
    _page(graph_layout, "code-graph/demo/file-system/src/x.py", FILE_X)
    for page in ("code-graph/demo/file-system/src/x.py", PAGE_FILE_Y):
        result = run_code_graph_neighborhood(graph_layout, page, 1)
        assert not any(e.source == FILE_X and e.target == FILE_Y for e in result.edges)
    assert {
        n.uri for n in run_code_graph_neighborhood(graph_layout, "code-graph/demo/file-system/src/x.py", 1).nodes
    } == {FILE_X}

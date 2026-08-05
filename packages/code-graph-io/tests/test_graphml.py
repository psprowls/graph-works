"""Unit tests for code_graph_io.graphml.to_graphml."""

from __future__ import annotations

import json
import sqlite3
import xml.etree.ElementTree as ET

import pytest
from code_graph_io.graphml import to_graphml
from code_graph_io.schema import apply_schema

_NS = "http://graphml.graphdrawing.org/graphml"


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    apply_schema(c)
    yield c
    c.close()


def _insert_node(conn, kind, name, path=None, line=None, attrs=None, uri=None):
    attrs_json = json.dumps(attrs) if attrs else None
    cur = conn.execute(
        "INSERT INTO nodes (kind, name, path, line, attrs_json, uri) VALUES (?,?,?,?,?,?)",
        (kind, name, path, line, attrs_json, uri),
    )
    return cur.lastrowid


def _insert_edge(conn, src, dst, kind, attrs=None):
    attrs_json = json.dumps(attrs) if attrs else None
    conn.execute(
        "INSERT INTO edges (src, dst, kind, attrs_json) VALUES (?,?,?,?)",
        (src, dst, kind, attrs_json),
    )


def test_empty_graph_is_valid_xml(conn):
    xml_str = to_graphml(conn)
    root = ET.fromstring(xml_str)
    assert root.tag == f"{{{_NS}}}graphml"


def test_edgedefault_directed(conn):
    xml_str = to_graphml(conn)
    root = ET.fromstring(xml_str)
    graph_el = root.find(f"{{{_NS}}}graph")
    assert graph_el is not None
    assert graph_el.get("edgedefault") == "directed"


def test_all_key_elements_declared(conn):
    xml_str = to_graphml(conn)
    root = ET.fromstring(xml_str)
    keys = {k.get("id"): k.get("for") for k in root.findall(f"{{{_NS}}}key")}
    # 8 node keys
    for attr in ("label", "kind", "name", "path", "line", "uri", "token_count", "attrs_json"):
        assert attr in keys, f"missing node key: {attr}"
        assert keys[attr] == "node"
    # 2 edge keys
    for attr in ("e_kind", "e_attrs_json"):
        assert attr in keys, f"missing edge key: {attr}"
        assert keys[attr] == "edge"


def test_node_count_matches(conn):
    _insert_node(conn, "file", "a.py", path="a.py", uri="file:a.py")
    _insert_node(conn, "function", "foo", path="a.py", line=10, uri=None)
    _insert_node(conn, "package", "mypkg", path="packages/mypkg", uri="pkg:local/mypkg")
    xml_str = to_graphml(conn)
    root = ET.fromstring(xml_str)
    graph_el = root.find(f"{{{_NS}}}graph")
    nodes = graph_el.findall(f"{{{_NS}}}node")
    assert len(nodes) == 3


def test_edge_count_matches(conn):
    a = _insert_node(conn, "file", "a.py", path="a.py")
    b = _insert_node(conn, "file", "b.py", path="b.py")
    c = _insert_node(conn, "file", "c.py", path="c.py")
    _insert_edge(conn, a, b, "imports")
    _insert_edge(conn, b, c, "imports")
    xml_str = to_graphml(conn)
    root = ET.fromstring(xml_str)
    graph_el = root.find(f"{{{_NS}}}graph")
    edges = graph_el.findall(f"{{{_NS}}}edge")
    assert len(edges) == 2


def test_token_count_surfaced_from_attrs_json(conn):
    _insert_node(conn, "function", "compute", path="a.py", line=5, attrs={"token_count": 42, "is_async": False})
    xml_str = to_graphml(conn)
    root = ET.fromstring(xml_str)
    graph_el = root.find(f"{{{_NS}}}graph")
    node_el = graph_el.find(f"{{{_NS}}}node")
    data = {d.get("key"): d.text for d in node_el.findall(f"{{{_NS}}}data")}
    assert data["token_count"] == "42"


def test_null_path_uri_absent(conn):
    _insert_node(conn, "function", "foo", path=None, uri=None)
    xml_str = to_graphml(conn)
    root = ET.fromstring(xml_str)
    graph_el = root.find(f"{{{_NS}}}graph")
    node_el = graph_el.find(f"{{{_NS}}}node")
    data_keys = {d.get("key") for d in node_el.findall(f"{{{_NS}}}data")}
    assert "path" not in data_keys
    assert "uri" not in data_keys


def test_attrs_json_roundtrips_verbatim(conn):
    blob = {"token_count": 7, "language": "python", "nested": [1, 2]}
    _insert_node(conn, "file", "x.py", path="x.py", attrs=blob)
    xml_str = to_graphml(conn)
    root = ET.fromstring(xml_str)
    graph_el = root.find(f"{{{_NS}}}graph")
    node_el = graph_el.find(f"{{{_NS}}}node")
    data = {d.get("key"): d.text for d in node_el.findall(f"{{{_NS}}}data")}
    assert json.loads(data["attrs_json"]) == blob


def test_edge_kind_and_attrs_present(conn):
    a = _insert_node(conn, "file", "a.py", path="a.py")
    b = _insert_node(conn, "file", "b.py", path="b.py")
    _insert_edge(conn, a, b, "imports", attrs={"weight": 1})
    xml_str = to_graphml(conn)
    root = ET.fromstring(xml_str)
    graph_el = root.find(f"{{{_NS}}}graph")
    edge_el = graph_el.find(f"{{{_NS}}}edge")
    assert edge_el is not None
    assert edge_el.get("source") == str(a)
    assert edge_el.get("target") == str(b)
    data = {d.get("key"): d.text for d in edge_el.findall(f"{{{_NS}}}data")}
    assert data["e_kind"] == "imports"
    assert json.loads(data["e_attrs_json"]) == {"weight": 1}


def test_two_edge_kinds(conn):
    a = _insert_node(conn, "file", "a.py", path="a.py")
    b = _insert_node(conn, "file", "b.py", path="b.py")
    c = _insert_node(conn, "function", "foo", path="a.py", line=1)
    _insert_edge(conn, a, b, "imports")
    _insert_edge(conn, a, c, "contains")
    xml_str = to_graphml(conn)
    root = ET.fromstring(xml_str)
    graph_el = root.find(f"{{{_NS}}}graph")
    edges = graph_el.findall(f"{{{_NS}}}edge")
    kinds = {d.text for e in edges for d in e.findall(f"{{{_NS}}}data") if d.get("key") == "e_kind"}
    assert kinds == {"imports", "contains"}

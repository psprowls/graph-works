"""GraphML serializer for the code graph.

`to_graphml(conn)` reads all nodes and edges from the SQLite DB and returns a
directed GraphML XML document as a string. Attributes are typed and declared
via <key> elements; the full attrs_json blob is always carried as a string
column so nothing is lost. Uses stdlib xml.etree only — no new dependency.
"""

from __future__ import annotations

import io
import json
import sqlite3
import xml.etree.ElementTree as ET

_NS = "http://graphml.graphdrawing.org/graphml"
ET.register_namespace("", _NS)

_NODE_KEYS: list[tuple[str, str]] = [
    ("label", "string"),
    ("kind", "string"),
    ("name", "string"),
    ("path", "string"),
    ("line", "int"),
    ("uri", "string"),
    ("token_count", "int"),
    ("attrs_json", "string"),
]

_EDGE_KEYS: list[tuple[str, str]] = [
    ("e_kind", "string"),
    ("e_attrs_json", "string"),
]


def _data(parent: ET.Element, key: str, value: object) -> None:
    if value is None:
        return
    d = ET.SubElement(parent, "data")
    d.set("key", key)
    d.text = str(value)


def to_graphml(conn: sqlite3.Connection) -> str:
    """Serialize the entire code graph to a GraphML XML string."""
    root = ET.Element(f"{{{_NS}}}graphml")

    for attr_id, attr_type in _NODE_KEYS:
        key_el = ET.SubElement(root, "key")
        key_el.set("id", attr_id)
        key_el.set("for", "node")
        key_el.set("attr.name", attr_id)
        key_el.set("attr.type", attr_type)

    for attr_id, attr_type in _EDGE_KEYS:
        key_el = ET.SubElement(root, "key")
        key_el.set("id", attr_id)
        key_el.set("for", "edge")
        key_el.set("attr.name", attr_id.removeprefix("e_"))
        key_el.set("attr.type", attr_type)

    graph_el = ET.SubElement(root, "graph")
    graph_el.set("id", "G")
    graph_el.set("edgedefault", "directed")

    node_rows = conn.execute("SELECT id, kind, name, path, line, attrs_json, uri FROM nodes").fetchall()

    for node_id, kind, name, path, line, attrs_json_str, uri in node_rows:
        node_el = ET.SubElement(graph_el, "node")
        node_el.set("id", str(node_id))

        _data(node_el, "label", name)
        _data(node_el, "kind", kind)
        _data(node_el, "name", name)
        _data(node_el, "path", path)
        _data(node_el, "line", line)
        _data(node_el, "uri", uri)

        token_count = None
        if attrs_json_str:
            try:
                attrs = json.loads(attrs_json_str)
                token_count = attrs.get("token_count")
            except (json.JSONDecodeError, AttributeError):
                pass
        _data(node_el, "token_count", token_count)
        _data(node_el, "attrs_json", attrs_json_str)

    edge_rows = conn.execute("SELECT src, dst, kind, attrs_json FROM edges").fetchall()

    for i, (src, dst, edge_kind, edge_attrs) in enumerate(edge_rows):
        edge_el = ET.SubElement(graph_el, "edge")
        edge_el.set("id", f"e{i}")
        edge_el.set("source", str(src))
        edge_el.set("target", str(dst))
        _data(edge_el, "e_kind", edge_kind)
        _data(edge_el, "e_attrs_json", edge_attrs)

    ET.indent(tree := ET.ElementTree(root), space="  ")
    buf = io.StringIO()
    tree.write(buf, encoding="unicode", xml_declaration=True)
    return buf.getvalue()

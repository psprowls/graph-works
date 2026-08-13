import json
from pathlib import Path

import pytest
from code_graph_io.parser import parse_file, to_graph_records

from ._fixture_loader import FIXTURES_ROOT


def _serialize_records(records) -> dict:
    return {
        "nodes": [
            {
                "kind": n.kind,
                "name": n.name,
                "path": n.path,
                "line": n.line,
                "attrs": n.attrs,
            }
            for n in records.nodes
        ],
        "edges": [{"src": list(e.src), "dst": list(e.dst), "kind": e.kind, "attrs": e.attrs} for e in records.edges],
    }


def _substitute(obj, path_str):
    if isinstance(obj, dict):
        return {k: _substitute(v, path_str) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, path_str) for v in obj]
    if obj == "PATH":
        return path_str
    return obj


GRAPH_FIXTURES = [
    ("python", "basic_function.py"),
    ("python", "class_with_decorator.py"),
    ("python", "same_file_method_collision.py"),
    ("python", "self_call.py"),
    ("javascript", "basic_function.js"),
    ("javascript", "esm_module.mjs"),
    ("javascript", "same_file_method_collision.js"),
    ("typescript", "basic_function.ts"),
    ("typescript", "interface_call.ts"),
    ("typescript", "type_exports.ts"),
    ("typescript", "exported_interface.ts"),
    ("typescript", "exported_type_alias.ts"),
    ("typescript", "exported_enum.ts"),
    ("typescript", "type_reexport.ts"),
]


@pytest.mark.parametrize(
    ("language", "fname"),
    GRAPH_FIXTURES,
    ids=[f"{lang}-{Path(f).stem}" for lang, f in GRAPH_FIXTURES],
)
def test_graph_projection(language, fname):
    fixture = FIXTURES_ROOT / language / fname
    expected_path = fixture.with_name(fixture.stem + ".graph.expected.json")
    tree = parse_file(fixture, package="fixtures")
    actual = _serialize_records(to_graph_records(tree))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected = _substitute(expected, str(fixture))
    assert actual["nodes"] == expected["nodes"]
    assert sorted(map(json.dumps, actual["edges"])) == sorted(map(json.dumps, expected["edges"]))


def test_emit_node_carries_byte_offsets() -> None:
    fixture = FIXTURES_ROOT / "python" / "basic_function.py"
    tree = parse_file(fixture, package="fixtures")
    records = to_graph_records(tree)

    file_node = next(n for n in records.nodes if n.kind == "file")
    assert file_node.start_byte == 0
    assert file_node.end_byte is not None and file_node.end_byte > 0

    fn = next(n for n in records.nodes if n.kind == "function" and n.name == "greet")
    assert fn.start_byte is not None
    assert fn.end_byte is not None
    assert fn.end_byte > fn.start_byte

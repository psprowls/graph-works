from pathlib import Path

from code_parser import parse_bytes


def test_python_broken_source_does_not_raise():
    tree = parse_bytes(b"def foo(\n", path=Path("broken.py"))
    assert tree.kind == "file"
    assert "parse_errors" in tree.attrs
    assert len(tree.attrs["parse_errors"]) > 0


def test_empty_python_file():
    tree = parse_bytes(b"", path=Path("empty.py"))
    assert tree.kind == "file"
    assert tree.children == []
    assert tree.refs == []


def test_javascript_broken_source_does_not_raise():
    tree = parse_bytes(b"function foo( {\n", path=Path("broken.js"))
    assert tree.kind == "file"
    assert "parse_errors" in tree.attrs

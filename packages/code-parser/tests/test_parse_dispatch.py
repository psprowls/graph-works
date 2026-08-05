from pathlib import Path

import pytest
from code_parser import parse_bytes, parse_file
from code_parser.parsers import EXTENSIONS, PARSERS


def test_parsers_registered():
    assert "python" in PARSERS
    assert "javascript" in PARSERS
    assert "typescript" in PARSERS


@pytest.mark.parametrize("ext", [".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"])
def test_extensions_covered(ext):
    assert ext in EXTENSIONS


def test_parse_file_dispatches_by_extension(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("def f(): pass\n")
    tree = parse_file(p, package="pkg")
    assert tree.language == "python"
    assert tree.kind == "file"


def test_parse_bytes_dispatches_by_extension():
    tree = parse_bytes(b"def f(): pass\n", path=Path("foo.py"))
    assert tree.language == "python"


def test_parse_bytes_language_override():
    tree = parse_bytes(b"def f(): pass\n", path=Path("foo.unknownext"), language="python")
    assert tree.language == "python"

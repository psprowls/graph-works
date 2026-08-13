from pathlib import Path

import pytest
from code_graph_io.parser import parse_bytes, parse_file
from code_graph_io.parser.parsers import EXTENSIONS, PARSERS


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


def test_resolve_call_target_defaults_to_a_no_op():
    """The base parser's hook returns the reference untouched.

    Only some languages can resolve a call within a file; the default must be
    identity so a parser that does not override it stays correct.
    """
    from code_graph_io.parser.parsers._base import LanguageParser
    from code_graph_io.parser.tree import Reference, Span

    class _Bare(LanguageParser):
        language = "bare"

        @property
        def grammar(self):  # pragma: no cover - never loaded
            raise NotImplementedError

        def parse(self, source, *, path, package=None):  # pragma: no cover - unused
            raise NotImplementedError

    ref = Reference(kind="call", target_name="f", target_module=None, site=Span(0, 1, 1, 1, 0, 1))
    assert _Bare().resolve_call_target(ref, file_tree=None) is ref

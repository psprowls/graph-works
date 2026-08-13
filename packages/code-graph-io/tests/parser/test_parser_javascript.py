import pytest
from code_graph_io.parser.parsers.javascript import JavaScriptParser

from ._fixture_loader import (
    diff,
    fixtures_for,
    load_expected,
    serialize_tree,
)

_PARSER = JavaScriptParser()
_FIXTURES = fixtures_for("javascript", (".js", ".jsx", ".mjs", ".cjs"))


def test_basic_metadata():
    assert _PARSER.name == "javascript"
    assert _PARSER.file_extensions == (".js", ".jsx", ".mjs", ".cjs")


def test_grammar_loadable():
    assert _PARSER.grammar is not None


@pytest.mark.parametrize("fixture", _FIXTURES, ids=lambda p: p.stem)
def test_fixture(fixture):
    source = fixture.read_bytes()
    tree = JavaScriptParser().parse(fixture, source, package="fixtures")
    actual = serialize_tree(tree)
    expected = load_expected(fixture)
    diffs = diff(actual, expected)
    assert diffs == [], "\n".join(diffs)

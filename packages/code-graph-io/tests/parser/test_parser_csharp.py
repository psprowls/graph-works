from pathlib import Path

import pytest
from code_graph_io.parser.parsers.csharp import CSharpParser

from ._fixture_loader import diff, fixtures_for, load_expected, serialize_tree

_FIXTURES = fixtures_for("csharp", (".cs",))


def test_basic_metadata():
    assert CSharpParser().name == "csharp"
    assert CSharpParser().file_extensions == (".cs",)


def test_grammar_loadable():
    assert CSharpParser().grammar is not None


def test_namespace_is_transparent_wrapper():
    source = b"namespace MyApp.Models {\n    class Widget {}\n}\n"
    tree = CSharpParser().parse(Path("Widget.cs"), source, package="fixtures")
    assert [c.name for c in tree.children] == ["Widget"]
    assert [c.kind for c in tree.children] == ["class"]


@pytest.mark.parametrize("fixture", _FIXTURES, ids=lambda p: p.stem)
def test_fixture(fixture):
    source = fixture.read_bytes()
    tree = CSharpParser().parse(fixture, source, package="fixtures")
    actual = serialize_tree(tree)
    expected = load_expected(fixture)
    diffs = diff(actual, expected)
    assert diffs == [], "\n".join(diffs)

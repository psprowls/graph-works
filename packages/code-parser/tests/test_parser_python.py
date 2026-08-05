import pytest
from _fixture_loader import (
    diff,
    fixtures_for,
    load_expected,
    serialize_tree,
)
from code_parser.parsers.python import PythonParser

_PARSER = PythonParser()
_FIXTURES = fixtures_for("python", (".py",))


def test_basic_metadata():
    assert _PARSER.name == "python"
    assert _PARSER.file_extensions == (".py",)


@pytest.mark.parametrize("fixture", _FIXTURES, ids=lambda p: p.stem)
def test_fixture(fixture):
    source = fixture.read_bytes()
    tree = PythonParser().parse(fixture, source, package="fixtures")
    actual = serialize_tree(tree)
    expected = load_expected(fixture)
    diffs = diff(actual, expected)
    assert diffs == [], "\n".join(diffs)

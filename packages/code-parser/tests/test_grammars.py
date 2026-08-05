import pytest
from code_parser import UnsupportedLanguageError
from code_parser.grammars import get_language
from tree_sitter import Language, Parser


def test_python_loads():
    lang = get_language("python")
    assert isinstance(lang, Language)
    parser = Parser(lang)
    tree = parser.parse(b"x = 1\n")
    assert tree.root_node is not None


def test_javascript_loads():
    lang = get_language("javascript")
    parser = Parser(lang)
    tree = parser.parse(b"const x = 1;\n")
    assert tree.root_node is not None


def test_typescript_grammar_cannot_parse_jsx():
    # The plain `typescript` grammar does NOT understand JSX — it produces an
    # error-laden tree. JSX-bearing files must use the `tsx` grammar instead.
    parser = Parser(get_language("typescript"))
    tree_ts = parser.parse(b"const x: number = 1;\n")
    assert not tree_ts.root_node.has_error
    tree_jsx = parser.parse(b"const X = () => <div>hi</div>;\n")
    assert tree_jsx.root_node.has_error


def test_tsx_grammar_parses_jsx_without_errors():
    lang = get_language("tsx")
    assert isinstance(lang, Language)
    parser = Parser(lang)
    tree = parser.parse(b"export function P() { return <div>x</div>; }\n")
    assert not tree.root_node.has_error


def test_unknown_language_raises():
    with pytest.raises(UnsupportedLanguageError):
        get_language("cobol")


def test_lookup_is_cached():
    a = get_language("python")
    b = get_language("python")
    assert a is b

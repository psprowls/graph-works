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


def test_grammar_load_failure_is_wrapped_as_unsupported_language(monkeypatch):
    """A known name whose grammar fails to load must not leak the raw exception.

    `get_language` is the only place the language-pack is touched; callers are
    promised a single error type, so a pack-level failure gets translated too —
    not just an unknown name.
    """
    import code_parser.grammars as grammars_module

    def _boom(_name):
        raise RuntimeError("pack is broken")

    monkeypatch.setattr(grammars_module, "_pack_get_language", _boom)
    grammars_module.get_language.cache_clear()
    try:
        with pytest.raises(UnsupportedLanguageError, match="Failed to load grammar"):
            grammars_module.get_language("python")
    finally:
        grammars_module.get_language.cache_clear()

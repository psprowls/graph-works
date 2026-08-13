from pathlib import Path

from code_graph_io.parser.parsers._config import LanguageConfig
from code_graph_io.parser.parsers._generic import generic_walk
from code_graph_io.parser.parsers.javascript import JavaScriptParser
from code_graph_io.parser.parsers.typescript import TypeScriptParser
from code_graph_io.parser.tree import SourceNode

_JS_CONFIG = LanguageConfig(
    grammar_name="javascript",
    language="javascript",
    class_types=frozenset({"class_declaration"}),
    function_types=frozenset({"function_declaration", "arrow_function"}),
    method_types=frozenset({"method_definition"}),
    import_types=frozenset({"import_statement"}),
    export_types=frozenset({"export_statement"}),
    call_types=frozenset({"call_expression"}),
    name_field="name",
    body_field="body",
    call_function_field="function",
    call_member_node_types=frozenset({"member_expression"}),
    call_member_field="property",
    function_boundary_types=frozenset({"function_declaration", "arrow_function", "method_definition"}),
)


def _walk(source: str) -> SourceNode:
    return generic_walk(
        _JS_CONFIG,
        Path("sample.js"),
        source.encode("utf-8"),
        package="my-pkg",
        language="javascript",
    )


def test_emits_file_root():
    root = _walk("")
    assert root.kind == "file"
    assert root.language == "javascript"
    assert root.package == "my-pkg"
    assert root.path == Path("sample.js")


def test_top_level_function():
    root = _walk("function foo() { return 1; }\n")
    names = [c.name for c in root.children if c.kind == "function"]
    assert "foo" in names
    foo = next(c for c in root.children if c.name == "foo")
    assert foo.kind == "function"
    assert foo.language == "javascript"
    assert foo.span.end_byte > foo.span.start_byte


def test_class_with_methods():
    root = _walk("class A { m() { return 1; } n() {} }\n")
    cls = next(c for c in root.children if c.kind == "class" and c.name == "A")
    method_names = sorted(m.name for m in cls.children if m.kind == "method")
    assert method_names == ["m", "n"]


def test_call_recorded_as_reference():
    root = _walk("function foo() { bar(); }\n")
    foo = next(c for c in root.children if c.name == "foo")
    targets = [r.target_name for r in foo.refs if r.kind == "call"]
    assert "bar" in targets


def test_import_recorded_as_reference():
    root = _walk("import { x } from './mod.js';\n")
    targets = [(r.target_name, r.target_module) for r in root.refs if r.kind == "import"]
    assert ("x", "./mod.js") in targets


def test_export_recorded_as_reference():
    root = _walk("export function foo() {}\nexport { bar };\n")
    kinds = [r.target_name for r in root.refs if r.kind == "export"]
    assert "foo" in kinds
    assert "bar" in kinds


def test_parse_errors_non_fatal():
    root = _walk("function foo( {\n")
    assert isinstance(root, SourceNode)
    assert "parse_errors" in root.attrs
    assert len(root.attrs["parse_errors"]) > 0


# --- Arrow-const / function-expression tests (RED-first; fixed in _generic.py) ---

_js = JavaScriptParser()
_ts = TypeScriptParser()


def test_top_level_arrow_const_is_function():
    source = "const isContains = (a, b) => a + b;\n"
    root = _js.parse(Path("sample.js"), source.encode("utf-8"), package="my-pkg")
    fn = next((c for c in root.children if c.name == "isContains"), None)
    assert fn is not None, "expected a child named 'isContains'"
    assert fn.kind == "function"
    assert fn.span.start_line == 1
    assert fn.path == Path("sample.js")


def test_exported_async_arrow_const_is_function():
    source = "export const isAtLocation = async (context, currentBatch, lastBatch) => {};\n"
    root = _js.parse(Path("sample.js"), source.encode("utf-8"), package="my-pkg")
    fn = next((c for c in root.children if c.name == "isAtLocation"), None)
    assert fn is not None, "expected a child named 'isAtLocation'"
    assert fn.kind == "function"
    assert fn.span.start_line == 1


def test_arrow_const_in_typescript():
    source = "const App = (): void => {};\n"
    root = _ts.parse(Path("sample.ts"), source.encode("utf-8"), package="my-pkg")
    fn = next((c for c in root.children if c.name == "App"), None)
    assert fn is not None, "expected a child named 'App'"
    assert fn.kind == "function"


def test_function_expression_const_is_function():
    source = "const f = function () {};\n"
    root = _js.parse(Path("sample.js"), source.encode("utf-8"), package="my-pkg")
    fn = next((c for c in root.children if c.name == "f"), None)
    assert fn is not None, "expected a child named 'f'"
    assert fn.kind == "function"


def test_export_arrow_does_not_leak_params():
    source = "export const isAtLocation = (context, currentBatch, lastBatch) => foo();\n"
    root = _js.parse(Path("sample.js"), source.encode("utf-8"), package="my-pkg")
    export_names = [r.target_name for r in root.refs if r.kind == "export"]
    assert "isAtLocation" in export_names
    for leaked in ("context", "currentBatch", "lastBatch", "foo"):
        assert leaked not in export_names, f"param/body identifier '{leaked}' leaked as export ref"

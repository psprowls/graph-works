from pathlib import Path

import pytest
from code_graph_io.parser.parsers.typescript import TypeScriptParser

from ._fixture_loader import (
    diff,
    fixtures_for,
    load_expected,
    serialize_tree,
)

_PARSER = TypeScriptParser()
_FIXTURES = fixtures_for("typescript", (".ts", ".tsx"))


def test_basic_metadata():
    assert _PARSER.name == "typescript"
    assert _PARSER.file_extensions == (".ts", ".tsx")


@pytest.mark.parametrize("fixture", _FIXTURES, ids=lambda p: p.stem)
def test_fixture(fixture):
    source = fixture.read_bytes()
    tree = TypeScriptParser().parse(fixture, source, package="fixtures")
    actual = serialize_tree(tree)
    expected = load_expected(fixture)
    diffs = diff(actual, expected)
    assert diffs == [], "\n".join(diffs)


def test_tsx_jsx_component_emits_exported_function():
    """A .tsx file with an exported JSX-returning function plus a nested arrow
    const must yield the outer function node and its export ref, with the nested
    arrow const as a CHILD of the function (not hoisted to file level).

    Regression for .tsx being parsed with the JSX-blind `typescript` grammar,
    which shreds the parse so the exported component is dropped and its nested
    arrow const is ejected to the file root (leaving only a pathless stub from
    barrel re-exports).
    """
    source = (
        b"export function Sheet({ visible, title, children }: SheetProps) {\n"
        b"    const onClose = () => {\n"
        b"        close();\n"
        b"    };\n"
        b"    return (\n"
        b"        <Modal visible={visible} onRequestClose={onClose}>\n"
        b'            <View className="flex-1">\n'
        b"                {title && (<Text>{title}</Text>)}\n"
        b"                {children}\n"
        b"            </View>\n"
        b"        </Modal>\n"
        b"    );\n"
        b"}\n"
    )
    tree = TypeScriptParser().parse(Path("Sheet.tsx"), source, package="fixtures")
    assert "parse_errors" not in tree.attrs
    top_level = {c.name for c in tree.children}
    assert top_level == {"Sheet"}
    assert "Sheet" in {r.target_name for r in tree.refs if r.kind == "export"}
    sheet = next(c for c in tree.children if c.name == "Sheet")
    assert "onClose" in {c.name for c in sheet.children}


def test_ts_file_still_uses_plain_typescript_grammar():
    """.ts files keep parsing cleanly (no regression from grammar routing)."""
    source = b"export interface Foo { a: number; }\nexport function bar(): void {}\n"
    tree = TypeScriptParser().parse(Path("x.ts"), source, package="fixtures")
    assert "parse_errors" not in tree.attrs
    names = {c.name for c in tree.children}
    assert {"Foo", "bar"} <= names

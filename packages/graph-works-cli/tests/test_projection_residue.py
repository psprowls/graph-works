"""No projection may creep back into graph-works-cli.

Every typed-result -> JSON projection lives in graph-works-wire, so the CLI
and graph-works-serve emit one shape. Stated structurally:

1. no module here defines a `*_payload` function, except the two
   CLI-introspection ones (the surface and help documents describe the CLI
   itself, not a result);
2. `json.dumps` is called only by the one encoder and by those two
   introspection sites -- so no command body can build a result dict and
   encode it inline;
3. nothing hands `encode()` a dict literal -- the same inline projection,
   one step removed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "graph_works_cli"
INTROSPECTION_PAYLOADS = {("util_cli/describe.py", "surface_payload"), ("cli.py", "_json_help_payload")}
JSON_DUMPS_ALLOWED = {"json_output.py", "cli.py", "util_cli/describe.py"}


def modules() -> list[Path]:
    found = sorted(SRC.rglob("*.py"))
    assert found
    return found


def rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", modules(), ids=rel)
def test_no_payload_function_outside_introspection(path: Path) -> None:
    defined = {
        node.name
        for node in ast.walk(tree(path))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.endswith("_payload")
    }
    stray = {name for name in defined if (rel(path), name) not in INTROSPECTION_PAYLOADS}
    assert not stray, f"{rel(path)} defines {sorted(stray)} -- projections belong in graph_works_wire"


@pytest.mark.parametrize("path", modules(), ids=rel)
def test_json_dumps_only_in_the_encoder_and_introspection(path: Path) -> None:
    if rel(path) in JSON_DUMPS_ALLOWED:
        return
    for node in ast.walk(tree(path)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "dumps":
            pytest.fail(f"{rel(path)}:{node.lineno} calls .dumps() -- encode through json_output.encode")
        if isinstance(node, ast.ImportFrom) and node.module == "json":
            pytest.fail(f"{rel(path)}:{node.lineno} imports from json")


@pytest.mark.parametrize("path", modules(), ids=rel)
def test_encode_is_never_handed_a_dict_literal(path: Path) -> None:
    for node in ast.walk(tree(path)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"encode", "emit"}
            and node.args
            and isinstance(node.args[0], ast.Dict | ast.DictComp)
        ):
            pytest.fail(f"{rel(path)}:{node.lineno} encodes a dict literal -- add a wire projection")

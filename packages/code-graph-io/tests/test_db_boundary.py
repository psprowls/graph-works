"""Enforce the code.db boundary invariant.

Only ``code-graph-io`` may build the ``code.db`` path, open a connection to it, or
run SQL against the code graph. Every other package reaches the graph through
``code_graph_io.open_reader`` / ``code_graph_io.open_writer`` and the re-exported handle
API, record dataclasses, and error classes. The conn-level modules (``queries``,
``upsert``, ``resolve``, ``store``, ``sync_wiki``, ``schema``) are
code-graph-io-internal.

This test walks every ``packages/*/src/**/*.py`` EXCEPT ``packages/code-graph-io/``
and asserts none reference ``code.db`` directly or import the forbidden
internal modules.
"""

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]  # tests -> code-graph-io -> packages -> repo root
_PKGS = _REPO / "packages"
_FORBIDDEN_MODULES = {
    "code_graph_io.store",
    "code_graph_io.queries",
    "code_graph_io.upsert",
    "code_graph_io.resolve",
    "code_graph_io.sync_wiki",
    "code_graph_io.schema",
}
_FORBIDDEN_NAMES = {"store", "queries", "upsert", "resolve", "sync_wiki", "schema"}


def _production_files():
    for src in _PKGS.glob("*/src"):
        if src.parts[-2] == "code-graph-io":
            continue
        yield from src.rglob("*.py")


_FILES = sorted(_production_files())


@pytest.mark.parametrize("path", _FILES, ids=lambda p: str(p.relative_to(_PKGS)))
def test_no_code_db_literal(path: Path):
    text = path.read_text()
    assert '"code.db"' not in text and "'code.db'" not in text, (
        f"{path} references code.db directly — go through code_graph_io.open_reader/open_writer"
    )


@pytest.mark.parametrize("path", _FILES, ids=lambda p: str(p.relative_to(_PKGS)))
def test_no_graph_io_internal_imports(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in _FORBIDDEN_MODULES:
                pytest.fail(f"{path} imports from {node.module}; use the code_graph_io handle API")
            if node.module == "code_graph_io":
                bad = {a.name for a in node.names} & _FORBIDDEN_NAMES
                assert not bad, (
                    f"{path} does `from code_graph_io import {', '.join(sorted(bad))}` — "
                    "those are internal; use the handle API"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in _FORBIDDEN_MODULES, f"{path} imports {alias.name}; use the code_graph_io handle API"

"""The lazy front door's table, its TYPE_CHECKING mirror and __all__ must agree (D-001)."""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import graph_works_core
import pytest

INIT = Path(graph_works_core.__file__)


def _type_checking_imports() -> dict[str, tuple[str, str]]:
    tree = ast.parse(INIT.read_text(encoding="utf-8"))
    found: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
            for stmt in node.body:
                assert isinstance(stmt, ast.ImportFrom) and stmt.module is not None
                for alias in stmt.names:
                    found[alias.asname or alias.name] = (stmt.module, alias.name)
    return found


def test_the_three_views_of_the_surface_agree() -> None:
    exports = graph_works_core._EXPORTS
    assert exports == _type_checking_imports()
    assert set(exports) == set(graph_works_core.__all__) - {"__version__"}


@pytest.mark.parametrize("name", sorted(graph_works_core._EXPORTS))
def test_each_target_resolves_to_the_object_the_front_door_returns(name: str) -> None:
    module, attribute = graph_works_core._EXPORTS[name]
    assert getattr(importlib.import_module(module), attribute) is getattr(graph_works_core, name)


def test_a_fetched_name_is_cached_in_the_module_globals() -> None:
    value = graph_works_core.ToolLoopResult
    assert vars(graph_works_core)["ToolLoopResult"] is value
    assert graph_works_core.ToolLoopResult is value


def test_dir_lists_the_whole_surface() -> None:
    assert set(graph_works_core.__all__) <= set(dir(graph_works_core))


def test_an_unknown_name_raises_attribute_error_naming_the_module() -> None:
    with pytest.raises(AttributeError, match=r"module 'graph_works_core' has no attribute 'no_such_name'"):
        graph_works_core.no_such_name  # noqa: B018
    with pytest.raises(ImportError):
        exec("from graph_works_core import no_such_name")


def test_the_orchestrate_alias_maps_to_the_plain_name() -> None:
    assert graph_works_core._EXPORTS["orchestrate_plan"] == ("graph_works_core.orchestrate.commands", "plan")


def test_a_star_import_binds_every_public_name_in_a_fresh_interpreter() -> None:
    code = (
        "ns = {}\n"
        "exec('from graph_works_core import *', ns)\n"
        "import graph_works_core as pkg\n"
        "missing = [n for n in pkg.__all__ if n not in ns]\n"
        "print(missing)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", check=True)
    assert done.stdout.strip() == "[]"

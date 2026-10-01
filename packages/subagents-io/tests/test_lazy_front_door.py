"""The lazy front door's table, its TYPE_CHECKING mirror and __all__ must agree (D-001)."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
import subagents_io

INIT = Path(subagents_io.__file__)


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
    exports = subagents_io._EXPORTS
    assert exports == _type_checking_imports()
    assert set(exports) == set(subagents_io.__all__) - {"__version__"}


@pytest.mark.parametrize("name", sorted(subagents_io._EXPORTS))
def test_each_target_resolves_to_the_object_the_front_door_returns(name: str) -> None:
    module, attribute = subagents_io._EXPORTS[name]
    assert getattr(importlib.import_module(module), attribute) is getattr(subagents_io, name)


def test_a_fetched_name_is_cached_in_the_module_globals() -> None:
    value = subagents_io.PlannedDispatch
    assert vars(subagents_io)["PlannedDispatch"] is value
    assert subagents_io.PlannedDispatch is value


def test_dir_lists_the_whole_surface() -> None:
    assert set(subagents_io.__all__) <= set(dir(subagents_io))


def test_an_unknown_name_raises_attribute_error_naming_the_module() -> None:
    with pytest.raises(AttributeError, match=r"module 'subagents_io' has no attribute 'no_such_name'"):
        subagents_io.no_such_name  # noqa: B018
    with pytest.raises(ImportError):
        exec("from subagents_io import no_such_name")

"""The lazy front door's table, its TYPE_CHECKING mirror and __all__ must agree (D-001)."""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import gw_kernel
import pytest

INIT = Path(gw_kernel.__file__)


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
    exports = gw_kernel._EXPORTS
    assert exports == _type_checking_imports()
    assert set(exports) == set(gw_kernel.__all__) - {"__version__"}


@pytest.mark.parametrize("name", sorted(gw_kernel._EXPORTS))
def test_each_target_resolves_to_the_object_the_front_door_returns(name: str) -> None:
    module, attribute = gw_kernel._EXPORTS[name]
    assert getattr(importlib.import_module(module), attribute) is getattr(gw_kernel, name)


def test_a_fetched_name_is_cached_in_the_module_globals() -> None:
    value = gw_kernel.WorkspaceLayout
    assert vars(gw_kernel)["WorkspaceLayout"] is value
    assert gw_kernel.WorkspaceLayout is value


def test_dir_lists_the_whole_surface() -> None:
    assert set(gw_kernel.__all__) <= set(dir(gw_kernel))


def test_an_unknown_name_raises_attribute_error_naming_the_module() -> None:
    with pytest.raises(AttributeError, match=r"module 'gw_kernel' has no attribute 'no_such_name'"):
        gw_kernel.no_such_name  # noqa: B018
    with pytest.raises(ImportError):
        exec("from gw_kernel import no_such_name")


def test_the_orchestrate_alias_maps_to_the_plain_name() -> None:
    assert "orchestrate_plan" not in gw_kernel._EXPORTS


def test_a_star_import_binds_every_public_name_in_a_fresh_interpreter() -> None:
    code = (
        "ns = {}\n"
        "exec('from gw_kernel import *', ns)\n"
        "import gw_kernel as pkg\n"
        "missing = [n for n in pkg.__all__ if n not in ns]\n"
        "print(missing)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", check=True)
    assert done.stdout.strip() == "[]"

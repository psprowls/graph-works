"""The band-1 contract, held mechanically by walking this package's own AST.

The root `[tool.importlinter]` carries the workspace half of this rule: an
`independence` contract over the five band-1 packages. `independence` is what
states it. An earlier revision of this docstring concluded grimp could not
express the rule at all, because a `layers` contract needs a total order among
the packages it names — true of `layers`, and the wrong contract type.
`independence` forbids every direction among its `modules` with no ordering.

Both mechanisms stay, because they fail in different directions. This walk
derives its sibling set from the filesystem, so a band-1 package added later is
covered with no edit — but it only sees imports *out of* this package. The
contract catches an edge introduced from the other side, but its `modules` list
is enumerated and a new package has to be added to it by hand.

The `os` assertion covers EVERY module, with no exemption, and so does the
sibling assertion. That is the payoff of injecting the price lookup, the trace
directory, the graph reader and the model factory instead of importing any of
them: config-io has to carve out `projection.py` for its atomic write; this
package carves out nothing. Third party is a separate rule with a
separate reason: nothing is imported from outside the stdlib at runtime, and
`langchain-core` — a dev-only dependency — may appear solely inside an
`if TYPE_CHECKING:` block, for the two annotations mypy resolves.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "subagents_io"
PACKAGES_DIR = Path(__file__).resolve().parents[3] / "packages"

#: Every module in the package. `test_the_module_list_is_complete` is what
#: makes a new module a decision rather than an omission.
ALL_MODULES = (
    "__init__.py",
    "adapters.py",
    "backend.py",
    "dispatch.py",
    "pool.py",
    "roles.py",
    "routing.py",
    "runner.py",
    "trace.py",
)

#: The one third-party root a module may import, and only under
#: `if TYPE_CHECKING:`. It is an ALLOWLIST for type-only imports; runtime
#: imports have none. See `test_third_party_imports_are_type_checking_only`.
TYPE_ONLY_ROOTS = {"langchain_core"}


def modules() -> list[Path]:
    found = sorted(SRC.rglob("*.py"))
    assert found, f"no modules found under {SRC} — the walk is looking in the wrong place"
    return found


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _roots(path: Path, *, type_checking_only: bool | None) -> set[str]:
    """First segment of every import; `None` = all, True/False = filter by block."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()

    def visit(node: ast.AST, guarded: bool) -> None:
        if isinstance(node, ast.Import | ast.ImportFrom):
            if type_checking_only is None or guarded == type_checking_only:
                if isinstance(node, ast.Import):
                    roots.update(alias.name.split(".")[0] for alias in node.names)
                elif node.level == 0 and node.module:
                    roots.add(node.module.split(".")[0])
            return
        for field, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            for child in children:
                if not isinstance(child, ast.AST):
                    continue
                in_guard = isinstance(node, ast.If) and field == "body" and _is_type_checking(node.test)
                visit(child, guarded or in_guard)

    visit(tree, False)
    return roots


def import_roots(path: Path) -> set[str]:
    """The first segment of every module this file imports."""
    return _roots(path, type_checking_only=None)


def sibling_import_names() -> set[str]:
    """Every workspace package's import name, derived from the filesystem.

    Derived rather than hardcoded so a package added later is covered without
    an edit here — a second, drifting source of truth is precisely what lets a
    boundary violation pass a boundary test.
    """
    names = {
        src_dir.name
        for pkg in PACKAGES_DIR.iterdir()
        if (pkg / "src").is_dir()
        for src_dir in (pkg / "src").iterdir()
        if src_dir.is_dir()
    }
    assert "subagents_io" in names, "the sibling walk did not find subagents_io — check PACKAGES_DIR"
    return names - {"subagents_io"}


def test_the_sibling_walk_sees_more_than_one_package():
    # Guards the guard: an empty sibling set would make the next test vacuous.
    assert len(sibling_import_names()) >= 2


def test_the_sibling_walk_sees_models_io():
    # The specific sibling this package is NOT allowed to import, and the one a
    # future reader is most likely to reach for. If the walk ever stops seeing
    # it, the next test stops meaning anything.
    assert "models_io" in sibling_import_names()


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_no_module_imports_another_workspace_package(module):
    # Including models_io. Cost accounting is injected, never imported —
    # taking that edge is a decision reserved for the strict-layer spike.
    assert not (import_roots(module) & sibling_import_names())


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_no_module_imports_os(module):
    # The band-1 rule, with no carve-out. The trace directory is a constructor
    # argument; nothing here reads an environment variable.
    assert "os" not in import_roots(module)


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_runtime_imports_are_stdlib_or_this_package(module):
    # There is no third-party allowlist at runtime: the package declares zero
    # runtime dependencies, so a bare third-party import fails here before it
    # can reach `pyproject.toml`.
    roots = _roots(module, type_checking_only=False) - set(sys.stdlib_module_names) - {"subagents_io"}
    assert not roots, f"{module.name} imports {sorted(roots)} at runtime"


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_third_party_imports_are_type_checking_only(module):
    roots = _roots(module, type_checking_only=True) - set(sys.stdlib_module_names) - {"subagents_io"}
    assert roots <= TYPE_ONLY_ROOTS, f"{module.name} imports {sorted(roots - TYPE_ONLY_ROOTS)} under TYPE_CHECKING"


def test_langchain_core_is_imported_under_type_checking_somewhere():
    # Guards the guard: if the annotations are ever dropped, the test above
    # would pass vacuously on every module rather than failing.
    assert any(_roots(m, type_checking_only=True) & TYPE_ONLY_ROOTS for m in modules())


def test_the_module_list_is_complete():
    # Adding a module without listing it here fails loudly rather than quietly
    # landing outside these expectations.
    assert {m.name for m in modules()} == set(ALL_MODULES)

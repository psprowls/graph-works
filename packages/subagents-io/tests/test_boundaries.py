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
package carves out nothing. The third-party allowlist is a separate rule with a
separate reason — `langchain-core` is not a workspace package, and declaring it
leaves the strict layer exactly where it was.
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

#: The one third-party root any module in this package may import — at runtime
#: or otherwise. It is an ALLOWLIST, not a watchlist; see
#: `test_langchain_core_is_the_only_third_party_import`.
THIRD_PARTY_ROOTS = {"langchain_core"}


def modules() -> list[Path]:
    found = sorted(SRC.rglob("*.py"))
    assert found, f"no modules found under {SRC} — the walk is looking in the wrong place"
    return found


def import_roots(path: Path) -> set[str]:
    """The first segment of every module this file imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


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
def test_langchain_core_is_the_only_third_party_import(module):
    # This replaces an assertion that `langchain_core` stayed behind
    # `if TYPE_CHECKING:`, which is now false by design: `runner.py`
    # constructs SystemMessage and HumanMessage, and that is why the package
    # declares a dependency at all.
    #
    # Strictly stronger than what it replaces. The old test intersected each
    # module's imports with THIRD_PARTY_ROOTS and inspected only what it found
    # there, so a brand-new third-party import passed it in silence. This one
    # subtracts the stdlib and this package's own name and asserts what is left
    # is a subset of the allowlist — a second dependency fails here before it
    # can reach `pyproject.toml`.
    roots = import_roots(module) - set(sys.stdlib_module_names) - {"subagents_io"}
    assert roots <= THIRD_PARTY_ROOTS, f"{module.name} imports {sorted(roots - THIRD_PARTY_ROOTS)}"


def test_langchain_core_is_actually_imported_somewhere():
    # Guards the guard: if the annotation is ever dropped, the test above would
    # pass vacuously on every module rather than failing.
    assert any(import_roots(m) & THIRD_PARTY_ROOTS for m in modules())


def test_the_module_list_is_complete():
    # Adding a module without listing it here fails loudly rather than quietly
    # landing outside these expectations.
    assert {m.name for m in modules()} == set(ALL_MODULES)

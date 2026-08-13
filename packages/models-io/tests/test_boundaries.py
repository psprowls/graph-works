"""The band-1 contract, held mechanically by walking this package's own AST.

The root `[tool.importlinter]` now carries the workspace half of this rule: an
`independence` contract over the five band-1 packages. It is not a substitute
for this walk — its `modules` list is enumerated, while the sibling set below is
derived from the filesystem, so a band-1 package added later is covered here
with no edit. The contract catches the converse: an edge introduced from the
other side, in a package this file never opens.

The `os` assertion covers EVERY module, with no exemption. That is the payoff
of making gateway credentials caller-supplied: config-io has to carve out
`projection.py` for its atomic write; this package carves out nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "models_io"
PACKAGES_DIR = Path(__file__).resolve().parents[3] / "packages"

#: Every module in the package. `test_the_module_list_is_complete` is what
#: makes a new module a decision rather than an omission.
ALL_MODULES = ("__init__.py", "bedrock.py", "errors.py", "loader.py", "normalize.py", "pricing.py", "vercel.py")

#: The two modules chartered to import a provider stack, and nothing else.
PROVIDER_MODULES = {"bedrock.py", "vercel.py"}

#: `langchain_core` is deliberately absent: loader.py imports it under
#: TYPE_CHECKING for a return annotation, which costs no runtime dependency
#: and drags in no provider stack.
PROVIDER_ROOTS = {"boto3", "botocore", "langchain_aws", "langchain_openai", "openai"}


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
    assert "models_io" in names, "the sibling walk did not find models_io — check PACKAGES_DIR"
    return names - {"models_io"}


def test_the_sibling_walk_sees_more_than_one_package():
    # Guards the guard: an empty sibling set would make the next test vacuous.
    assert len(sibling_import_names()) >= 2


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_no_module_imports_another_workspace_package(module):
    assert not (import_roots(module) & sibling_import_names())


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_no_module_imports_os(module):
    # The band-1 rule, with no carve-out. Gateway credentials are arguments;
    # AWS credentials are boto3's to resolve, below this package.
    assert "os" not in import_roots(module)


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_only_the_two_provider_modules_import_a_provider(module):
    found = import_roots(module) & PROVIDER_ROOTS
    if module.name in PROVIDER_MODULES:
        assert found, f"{module.name} is a provider module but imports no provider"
    else:
        assert not found, f"{module.name} imports {sorted(found)}"


def test_the_module_list_is_complete():
    # Adding a module without listing it here fails loudly rather than quietly
    # landing outside PROVIDER_MODULES' expectations.
    assert {m.name for m in modules()} == set(ALL_MODULES)

"""The band-1 contract, held mechanically by walking this package's own AST.

The root `[tool.importlinter]` now carries the workspace half of this rule: an
`independence` contract over the five band-1 packages. It is not a substitute
for this walk — its `modules` list is enumerated, while the sibling set below is
derived from the filesystem, so a band-1 package added later is covered here
with no edit. The contract catches the converse: an edge introduced from the
other side, in a package this file never opens.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "config_io"
PACKAGES_DIR = Path(__file__).resolve().parents[3] / "packages"

#: projection.py is the one exemption: it needs `os.fdopen` for the atomic
#: tempfile write. Writing a file atomically is not reading the environment,
#: which is what the band-1 rule actually forbids.
#:
#: The design spec names four modules; errors.py is the fifth, added here for
#: free — everything on the resolution path imports it, so exempting it would
#: leave a hole under the modules that are checked.
RESOLUTION_PATH = ("entries.py", "registry.py", "dotted.py", "store.py", "errors.py")

#: Everything not on the resolution path. `test_the_resolution_path_list_is_complete`
#: is what makes a new module a decision rather than an omission.
EXEMPT = ("__init__.py", "projection.py")


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
    assert "config_io" in names, "the sibling walk did not find config_io — check PACKAGES_DIR"
    return names - {"config_io"}


def test_the_sibling_walk_sees_more_than_one_package():
    # Guards the guard: an empty sibling set would make the next test vacuous.
    assert len(sibling_import_names()) >= 2


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_no_module_imports_another_workspace_package(module):
    assert not (import_roots(module) & sibling_import_names())


@pytest.mark.parametrize("module", [SRC / name for name in RESOLUTION_PATH], ids=RESOLUTION_PATH)
def test_the_resolution_path_never_imports_os(module):
    # config-io reads only the variables the caller named, from a mapping the
    # caller handed it. `environ` being required is what makes that true; this
    # is what keeps it true.
    assert "os" not in import_roots(module)


def test_only_the_shipped_store_imports_a_yaml_library():
    # `ruamel.yaml` is the single runtime dependency and exists only for
    # PlainYamlStore; a caller supplying its own store never loads it. The
    # import root is `ruamel`, not `yaml` -- `from ruamel.yaml import YAML`
    # would slip past a check keyed on the old name.
    importers = {module.name for module in modules() if {"ruamel", "yaml"} & import_roots(module)}
    assert importers == {"store.py"}


def test_the_resolution_path_list_is_complete():
    # Every module is either on the resolution path or explicitly exempt, so
    # adding a module without deciding its status fails here rather than
    # silently landing outside the os check.
    assert {m.name for m in modules()} == set(RESOLUTION_PATH) | set(EXEMPT)

"""This package's contract, held by walking its own AST.

The workspace half is the root `[tool.importlinter]`: a `forbidden` contract
so `subagents_io` never imports a backend, and an `independence` contract so
the two backends never import each other. Both mechanisms stay, because they
fail in different directions — this walk derives its sibling set from the
filesystem, so a package added later is covered with no edit, but it only sees
imports *out of* this package.

The rule here is narrower than band 1's and deliberately so. This is the
package where vendor and system coupling is permitted to live, so `subprocess`
is allowed — in exactly one module. What is *not* allowed is a second
workspace edge or any third party at all: `subagents_io` is the protocol this
implements, and the `orca` CLI is a process, not a library.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "workflow_orca"
PACKAGES_DIR = Path(__file__).resolve().parents[3] / "packages"

ALL_MODULES = ("__init__.py", "_cli.py", "_map.py", "backend.py")

#: The one workspace package this may import. There is no third-party
#: allowlist because the list is empty.
ALLOWED_WORKSPACE = {"subagents_io"}


def modules() -> list[Path]:
    found = sorted(SRC.rglob("*.py"))
    assert found, f"no modules found under {SRC} — the walk is looking in the wrong place"
    return found


def import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def sibling_import_names() -> set[str]:
    names = {
        src_dir.name
        for pkg in PACKAGES_DIR.iterdir()
        if (pkg / "src").is_dir()
        for src_dir in (pkg / "src").iterdir()
        if src_dir.is_dir()
    }
    assert "workflow_orca" in names, "the sibling walk did not find workflow_orca — check PACKAGES_DIR"
    return names - {"workflow_orca"}


def test_the_sibling_walk_sees_the_other_backend():
    # The specific sibling this package is NOT allowed to import, and the one
    # a future reader is most likely to reach for. If the walk stops seeing
    # it, the next test stops meaning anything.
    assert "workflow_local" in sibling_import_names()


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_subagents_io_is_the_only_workspace_import(module):
    assert (import_roots(module) & sibling_import_names()) <= ALLOWED_WORKSPACE


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_no_third_party_import_anywhere(module):
    roots = import_roots(module) - set(sys.stdlib_module_names) - {"workflow_orca"} - ALLOWED_WORKSPACE
    assert not roots, f"{module.name} imports {sorted(roots)}"


def test_subprocess_lives_in_exactly_one_module():
    # Vendor coupling is permitted here, but it stays behind the transport
    # seam: `_cli.py` is the only module that runs a process, which is what
    # lets every other test drive real argv against captured JSON.
    owners = {m.name for m in modules() if "subprocess" in import_roots(m)}
    assert owners == {"_cli.py"}


def test_the_module_list_is_complete():
    assert {m.name for m in modules()} == set(ALL_MODULES)

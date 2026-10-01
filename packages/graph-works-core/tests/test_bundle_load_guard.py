"""No interface-or-application module loads a bundle except through `graph_works_core.workspace.bundle`.

AST, not grep: a comment naming `load_bundle()` is not a load, and an aliased import (`load_bundle as _load_bundle`) is.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]
SRC_ROOTS = tuple(PACKAGES / name / "src" for name in ("graph-works-core", "graph-works-cli", "graph-works-serve"))
ALLOWED = PACKAGES / "graph-works-core" / "src" / "graph_works_core" / "workspace" / "bundle.py"


def offenders(paths: list[Path]) -> list[str]:
    found: list[str] = []
    for path in paths:
        if path == ALLOWED:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                names = {alias.name for alias in node.names}
                bare = node.module == "okf_io" and "load_bundle" in names
                if bare or (node.module == "okf_io.bundle" and "load" in names):
                    found.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.Attribute) and node.attr == "load_bundle":
                if isinstance(node.value, ast.Name) and node.value.id == "okf_io":
                    found.append(f"{path}:{node.lineno}")
    return found


def _sources() -> list[Path]:
    paths = sorted(path for root in SRC_ROOTS for path in root.rglob("*.py"))
    assert len(paths) > 100, "the walk is looking in the wrong place"
    return paths


def test_no_module_loads_a_bundle_bare() -> None:
    assert offenders(_sources()) == []


def test_the_guard_catches_an_injected_bare_load(tmp_path: Path) -> None:
    module = tmp_path / "injected.py"
    module.write_text("from okf_io import load_bundle\n\nload_bundle(root)\n", encoding="utf-8", newline="")
    aliased = tmp_path / "aliased.py"
    aliased.write_text("from okf_io import load_bundle as _lb\n", encoding="utf-8", newline="")
    attribute = tmp_path / "attribute.py"
    attribute.write_text("import okf_io\n\nokf_io.load_bundle(root)\n", encoding="utf-8", newline="")
    assert len(offenders([module, aliased, attribute])) == 3


def test_a_comment_is_not_a_load(tmp_path: Path) -> None:
    module = tmp_path / "comment.py"
    module.write_text("# load_bundle(root) is how okf-io loads\nx = 1\n", encoding="utf-8", newline="")
    assert offenders([module]) == []

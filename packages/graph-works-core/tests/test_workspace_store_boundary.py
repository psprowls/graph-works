"""D-004 as a check: `PlainYamlStore` is constructed in exactly two places.

`manifest_store` / `workspace_store` are a **read** seam that also hands back
the write-side layers: `config_io.set_key` is read-mutate-write over
`store.read()`, and `LayeredYamlStore` is deliberately read-only, so a write
routed through the merged view becomes a `TypeError`. A caller that needs to
write names a layer instead — `graph_works_cli.config_cli.main._store` reads
`.base` / `.overlay` off the `LayeredYamlStore` `workspace_store` already
built, rather than constructing its own `PlainYamlStore`, which is why that
module is not one of the two permitted sites below. The type system cannot
draw this line -- every `config-io` entry point takes `ConfigStore`, and
`ConfigStore` declares `write`/`snapshot`/`restore`, so a factory returning it
typechecks at a write just as well as at a read. Hence a walk.

AST and not grep, so an aliased import (`from config_io import PlainYamlStore
as _S`) cannot slip past. Scope is shipped source only: `scripts/` is repo
tooling, not a package consumer, and is deliberately outside the walk.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGES_DIR = Path(__file__).resolve().parents[3] / "packages"

#: Every site in `packages/*/src` allowed to construct the base store, as
#: `<import name>/<path within src>:<qualified function>`.
PERMITTED = {
    "graph_works_core/workspace/manifest.py:dispatch_store",
    "graph_works_core/workspace/manifest.py:manifest_store",
    "graph_works_core/workspace/manifest.py:set_value",
}


def _source_roots() -> list[Path]:
    roots = sorted(
        src_dir
        for pkg in PACKAGES_DIR.iterdir()
        if (pkg / "src").is_dir()
        for src_dir in (pkg / "src").iterdir()
        if src_dir.is_dir()
    )
    assert len(roots) >= 10, f"the package walk found only {len(roots)} roots -- check PACKAGES_DIR"
    return roots


def _aliases(tree: ast.Module) -> set[str]:
    """Every local name bound to `config_io.store.PlainYamlStore` in this module."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").startswith("config_io"):
            names.update(alias.asname or alias.name for alias in node.names if alias.name == "PlainYamlStore")
    return names


def _enclosing(tree: ast.Module, target: ast.AST) -> str:
    """The name of the innermost function containing *target*, or `<module>`."""
    enclosing = "<module>"
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            child is target for child in ast.walk(node)
        ):
            enclosing = node.name
    return enclosing


def construction_sites() -> set[str]:
    sites: set[str] = set()
    for root in _source_roots():
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            names = _aliases(tree)
            if not names:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names:
                    module_id = path.relative_to(root.parent).as_posix()
                    sites.add(f"{module_id}:{_enclosing(tree, node)}")
    return sites


def test_the_walk_finds_the_store_at_all() -> None:
    # Guards the guard: an empty result would make the next test vacuous.
    assert construction_sites()


def test_only_the_declared_store_factories_and_write_site_construct_the_base_store() -> None:
    assert construction_sites() == PERMITTED

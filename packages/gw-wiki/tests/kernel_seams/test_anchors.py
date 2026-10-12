"""Wiki platform capability checks against kernel seams."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from gw_wiki.util.platform import POSIX_ONLY_MODULES


def _module_scope_imports(source: str) -> set[str]:
    """Top-level import names only -- a function-scope import is not the hazard."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _module_source(dotted: str) -> str:
    spec = importlib.util.find_spec(dotted)
    assert spec is not None and spec.origin is not None
    return Path(spec.origin).read_text(encoding="utf-8")


def test_no_workspace_module_imports_a_posix_only_module_at_module_scope() -> None:
    """The transitive closure, asserted once rather than per module."""
    package = Path(importlib.util.find_spec("gw_kernel.workspace").origin).parent
    for module_path in sorted(package.glob("*.py")):
        offending = _module_scope_imports(module_path.read_text(encoding="utf-8")) & POSIX_ONLY_MODULES
        assert offending == set(), f"{module_path.name} imports {sorted(offending)} at module scope"


def test_anchors_imports_no_posix_only_module_at_module_scope() -> None:
    """The hazard is transitive: `transactions` imports `anchors`.

    The sibling item asserted this property against `transactions.py` alone,
    which passed vacuously -- `import fcntl` at `anchors.py` module scope
    still killed `import transactions` on native Windows before argv was
    parsed.  Both halves are asserted now.
    """
    source = _module_source("gw_kernel.workspace.anchors")
    offending = _module_scope_imports(source) & POSIX_ONLY_MODULES
    assert offending == set(), f"anchors.py imports POSIX-only modules at module scope: {sorted(offending)}"


def test_transactions_imports_no_posix_only_module_at_module_scope() -> None:
    """On native Windows the engine must *import* and fail at call time.

    Asserted against the module source rather than a mocked import: a mock can
    pass vacuously, and this property is the reason the anchor seam exists.
    """
    source = _module_source("gw_pipeline.transactions")
    offending = _module_scope_imports(source) & POSIX_ONLY_MODULES
    assert offending == set(), f"transactions.py imports POSIX-only modules at module scope: {sorted(offending)}"

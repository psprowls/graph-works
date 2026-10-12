"""The slice imports only workspace primitives and its declared lower packages."""

from __future__ import annotations

import ast
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src/gw_orchestrate"
LOWER = frozenset({"work_tracker_okf", "okf_io", "okf_ext", "subagents_io"})


def offenders(source: str, *, module: str) -> list[str]:
    found = []
    package = module.rpartition(".")[0]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if node.level:
                try:
                    target = resolve_name("." * node.level + target, package)
                except ImportError:
                    found.append(ast.unparse(node))
                    continue
            targets = [target]
        else:
            continue
        for target in targets:
            root = target.split(".")[0]
            if (
                root in sys.stdlib_module_names
                or root in LOWER
                or root == "gw_pipeline"
                or target == "gw_orchestrate"
                or target.startswith("gw_orchestrate.")
                or target == "gw_kernel.workspace"
                or target.startswith("gw_kernel.workspace.")
            ):
                continue
            found.append(target)
    return found


@pytest.mark.parametrize(
    "source",
    [
        "import gw_kernel",
        "from gw_kernel import workspace",
        "from gw_work import commands",
        "import gw_kernel as kernel",
        "import gw_cli",
        "import undeclared_external",
        "import gw_kernel.workspace_other",
        "import gw_orchestrate_other",
        "if TYPE_CHECKING:\n    import gw_work as work",
        "def nested():\n    from gw_kernel import workspace",
        "from ..gw_kernel import workspace",
    ],
)
def test_rejects_disallowed_imports(source: str) -> None:
    assert offenders(source, module="gw_orchestrate.commands")


@pytest.mark.parametrize(
    "source",
    [
        "import gw_pipeline.transactions as tx",
        "from gw_pipeline import finish",
        "import os",
        "from pathlib import Path",
        "from gw_kernel.workspace import layout",
        "import gw_kernel.workspace.layout as layout",
        "from . import commands",
        "from .commands import plan",
        "from work_tracker_okf import pipeline",
        "# import gw_kernel\ntext = 'import gw_kernel'",
    ],
)
def test_accepts_legal_imports(source: str) -> None:
    assert offenders(source, module="gw_orchestrate.commands") == []


def test_shipped_imports_obey_the_boundary() -> None:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        module = ".".join(("gw_orchestrate", *path.relative_to(SRC).with_suffix("").parts))
        found.extend(f"{path}:{target}" for target in offenders(path.read_text(encoding="utf-8"), module=module))
    assert found == []

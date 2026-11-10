"""The slice imports only workspace primitives and its declared lower packages."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from gw_kernel.testing.boundaries import discovery_lookups, manifest_reads, module_name, scan_imports, under

SRC = Path(__file__).resolve().parents[1] / "src/gw_orchestrate"
LOWER = frozenset({"work_tracker_okf", "okf_io", "okf_ext", "subagents_io"})


def offenders(source: str, *, module: str) -> list[str]:
    scan = scan_imports(source, module=module)
    found = list(scan.unresolved)
    for target in scan.targets:
        root = target.split(".")[0]
        if (
            root in sys.stdlib_module_names
            or root in LOWER
            or root == "gw_pipeline"
            or under(target, "gw_orchestrate")
            or under(target, "gw_kernel.workspace")
        ):
            continue
        found.append(target)
    found.extend(finding.text for finding in discovery_lookups(scan.tree))
    found.extend(finding.text for finding in manifest_reads(scan.tree, scan.aliases))
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
        "Path('workspace.yaml').read_text()",
        "open(root / 'workspace.yaml')",
        "from pathlib import Path\nmanifest = root / 'workspace.yaml'\nmanifest.read_bytes()",
        "import os\nos.environ['GRAPH_WORKS_DIR']",
        "import os\nos.getenv('GRAPH_WORKS_DIR')",
    ],
)
def test_rejects_manifest_reads_and_discovery_lookups(source: str) -> None:
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
        "text = 'workspace.yaml'",
    ],
)
def test_accepts_legal_imports(source: str) -> None:
    assert offenders(source, module="gw_orchestrate.commands") == []


def test_shipped_imports_obey_the_boundary() -> None:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        module = module_name(path, SRC, package="gw_orchestrate")
        found.extend(f"{path}:{target}" for target in offenders(path.read_text(encoding="utf-8"), module=module))
    assert found == []


def test_finish_consolidation():
    roots = [SRC, Path(__file__).resolve().parents[2] / "gw-pipeline/src/gw_pipeline"]
    refused_classes = 0
    for root in roots:
        for file in root.rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == "FinishRefused":
                    refused_classes += 1
                if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_TERMINAL" for t in node.targets
                ):
                    pytest.fail(f"{file}: _TERMINAL assignment")
                if isinstance(node, ast.FunctionDef):
                    assert not (file.name == "accept_integration.py" and node.name == "_resolve")
                    assert not (file.name == "integrate.py" and node.name == "_sha")
                assert not (isinstance(node, ast.Name) and node.id == "observe_integration")
                assert not (isinstance(node, ast.FunctionDef) and node.name == "observe_integration")
    assert refused_classes == 1

"""The work slice consumes resolved kernel primitives, never sibling slices."""

from __future__ import annotations

from pathlib import Path

import pytest
from gw_kernel.testing.boundaries import discovery_lookups, manifest_reads, module_name, scan_imports, under

SRC = Path(__file__).resolve().parents[1] / "src/gw_work"
TESTS = Path(__file__).resolve().parent
SIBLINGS = {"gw_orchestrate", "gw_wiki"}
INTERFACES = {"gw_cli", "gw_serve", "gw_wire"}
DISCOVERY = "gw_kernel.workspace.discovery"


def offenders(source: str, *, module: str, test: bool = False) -> list[str]:
    scan = scan_imports(source, module=module)
    found = list(scan.unresolved)
    found.extend(target for target in scan.targets if target.split(".")[0] in SIBLINGS)
    if not test:
        found.extend(
            target
            for target in scan.targets
            if target.split(".")[0] in INTERFACES or target == "gw_kernel" or under(target, DISCOVERY)
        )
        found.extend(projection for projection in scan.projections if under(projection, DISCOVERY))
        found.extend(finding.text for finding in discovery_lookups(scan.tree))
        found.extend(finding.text for finding in manifest_reads(scan.tree, scan.aliases))
    return found


@pytest.mark.parametrize(
    "source",
    [
        "import gw_orchestrate as sibling",
        "from gw_wiki import commands",
        "from gw_orchestrate.commands import run",
        "if TYPE_CHECKING:\n    import gw_wiki.commands",
        "from ..gw_orchestrate import commands",
        "import gw_kernel as kernel",
        "from gw_kernel import workspace",
        "import gw_wire as wire",
        "from gw_kernel.workspace import discovery as d",
        "from gw_kernel.workspace.discovery import resolve as r",
        "import gw_kernel.workspace as ws\nws.discovery.resolve()",
        "from gw_kernel import workspace as ws\nws.discovery.resolve()",
        "import os\nos.environ['GRAPH_WORKS_DIR']",
        "import os\nos.getenv('GRAPH_WORKS_DIR')",
        "Path('workspace.yaml').read_text()",
        "(root / 'workspace.yaml').open()",
        "open(root / 'workspace.yaml')",
        'from builtins import open as read_manifest\nread_manifest("workspace.yaml", encoding="utf-8")',
        'from pathlib import Path\nmanifest = Path("workspace.yaml")\nmanifest.read_text(encoding="utf-8")',
        "from pathlib import Path\ndef read_manifest():\n"
        '    manifest = Path("workspace.yaml")\n    return manifest.read_text()\n',
        'from pathlib import Path\nmanifest = Path("workspace.yaml")\n'
        'def read_notes():\n    manifest = Path("notes.md")\n    return manifest.read_text()\n'
        "def read_manifest():\n    return manifest.read_text()\n",
        'from pathlib import Path\nmanifest = Path("workspace.yaml")\n'
        'class Notes:\n    manifest = Path("notes.md")\nmanifest.read_text()\n',
        "from pathlib import Path\nclass Manifest:\n"
        '    manifest = Path("workspace.yaml")\n    text = manifest.read_text()\n',
    ],
)
def test_rejects_forbidden_source(source):
    assert offenders(source, module="gw_work.commands")


@pytest.mark.parametrize(
    "source",
    ["import gw_orchestrate", "from gw_wiki import commands", "if TYPE_CHECKING:\n    from gw_orchestrate import gate"],
)
def test_rejects_sibling_test_imports(source):
    assert offenders(source, module="gw_work.tests.example", test=True)


@pytest.mark.parametrize(
    "source",
    [
        "from gw_kernel.workspace.layout import WorkspaceLayout",
        "from gw_pipeline import transactions",
        "from gw_pipeline.transactions import apply_mutation",
        "from . import commands",
        "from .archive.commands import run_archive",
        "import os",
        "# import gw_wiki\ntext = 'workspace.yaml'",
        'from builtins import open as read_manifest\nread_manifest("notes.md", encoding="utf-8")',
        'from pathlib import Path\nnotes = Path("notes.md")\nnotes.read_text(encoding="utf-8")',
        'from pathlib import Path\nmanifest = Path("workspace.yaml")\n'
        'manifest = Path("notes.md")\nmanifest.read_text()',
        'from pathlib import Path\nmanifest = Path("notes.md")\n'
        'def build_manifest_path():\n    manifest = Path("workspace.yaml")\n    return manifest\n'
        'def read_notes():\n    return manifest.read_text(encoding="utf-8")\n',
        "from pathlib import Path\ndef build_manifest_path():\n"
        '    path = Path("workspace.yaml")\n    return path\n'
        'def read_notes(path):\n    return path.read_text(encoding="utf-8")\n',
        'from pathlib import Path\nmanifest = Path("workspace.yaml")\n'
        "def read_notes(manifest):\n    return manifest.read_text()\n",
        'from pathlib import Path\nmanifest = Path("notes.md")\n'
        'class Manifest:\n    manifest = Path("workspace.yaml")\nmanifest.read_text()\n',
        "from pathlib import Path\nclass Manifest:\n"
        '    path = Path("workspace.yaml")\n    def read_notes(self, path):\n        return path.read_text()\n',
    ],
)
def test_accepts_defining_modules(source):
    assert offenders(source, module="gw_work.commands") == []


def test_shipped_source_and_tests_obey_boundary():
    assert SRC.is_dir(), "missing work source root"
    found = []
    for root, test in ((SRC, False), (TESTS, True)):
        for path in sorted(root.rglob("*.py")):
            module = module_name(path, root, package="gw_work")
            found.extend(
                f"{path}:{target}" for target in offenders(path.read_text(encoding="utf-8"), module=module, test=test)
            )
    assert found == []

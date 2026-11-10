"""AST boundaries supplement grimp's missing ancestor edges.

Static syntax only: dynamic imports and runtime string construction are outside
this guard. Fixtures live in throwaway source trees, never shipped modules.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest
from gw_kernel.testing.boundaries import discovery_lookups, manifest_reads, module_name, scan_imports

PACKAGE = Path(__file__).resolve().parents[1]
SRC = PACKAGE / "src" / "gw_wiki"
KERNEL_SEAMS = frozenset({"workspace", "graph", "read_session", "guidance"})
INTERFACES = frozenset({"gw_cli", "gw_serve", "gw_wire"})
DECLARED = frozenset(
    dependency.split("[", 1)[0].split(">", 1)[0].replace("-", "_")
    for dependency in tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
)


def _offenders(paths: list[Path], *, tests: bool = False) -> list[str]:
    found: list[str] = []
    for path in paths:
        source_root = next((parent for parent in path.parents if parent.name == "src"), None)
        module = module_name(path, source_root) if source_root else f"gw_wiki.{path.stem}"
        scan = scan_imports(path.read_text(encoding="utf-8"), module=module)
        for fact in scan.imports:
            names = fact.targets if fact.resolved else fact.raw
            if not fact.resolved or any(not _permitted(name, tests=tests) for name in names):
                found.append(f"{path}:{fact.line} import boundary")
        if not tests:
            found.extend(
                f"{path}:{finding.line} workspace discovery"
                for finding in (*discovery_lookups(scan.tree), *manifest_reads(scan.tree, scan.aliases))
            )
    return found


def _permitted(target: str, *, tests: bool) -> bool:
    root, _, child = target.partition(".")
    if tests:
        return root not in INTERFACES | {"gw_orchestrate"}
    if root == "gw_kernel":
        return bool(child) and child.split(".", 1)[0] in KERNEL_SEAMS
    return root in sys.stdlib_module_names | {"__future__", "gw_wiki"} | (DECLARED - {"gw_kernel"})


def _fixture(tmp_path: Path, source: str, module: str = "gw_wiki/query/injected.py") -> Path:
    path = tmp_path / "src" / module
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source + "\n", encoding="utf-8", newline="")
    return path


@pytest.mark.parametrize(
    "source",
    [
        "import gw_work",
        "from gw_orchestrate import X",
        "import gw_cli",
        "from gw_serve import routes",
        "from gw_wire import project",
        "from gw_kernel import resolve",
        "import gw_kernel",
        "from gw_kernel import work",
        "from gw_kernel.events import classify",
        "from gw_kernel import workspace as seam",
        "from ...gw_kernel import resolve",
        "import unknown_dependency",
    ],
)
def test_source_rejects_forbidden_imports(tmp_path: Path, source: str) -> None:
    path = _fixture(tmp_path, source)
    assert _offenders([path]) == [f"{path}:1 import boundary"]


@pytest.mark.parametrize(
    "source",
    [
        "from gw_kernel.workspace import layout as model",
        "from gw_kernel.graph.reader import GraphReader",
        "from gw_kernel.read_session import open_read_session",
        "from gw_kernel.guidance import claims",
        "from . import prompts",
        "from ..agent_substrate import roles",
        "import os",
        "from pathlib import Path",
        "from code_graph_io import store",
        "from langchain_core import tools",
        "text = 'from gw_work import X'",
        "# import gw_work",
        "from gw_kernel.workspace.discovery import resolve\nresolve(workspace=path)",
        "import os\nkey = 'GRAPH_WORKS_DIR'",
        "from pathlib import Path\nname = 'workspace.yaml'",
        "import os\nos.getenv('AI_GATEWAY_API_KEY')",
        "import os\nos.getenv(key='AI_GATEWAY_API_KEY')",
        "from os import getenv as read_env\nread_env(key='AI_GATEWAY_API_KEY')",
        "from os import environ as env\nenv.get(key='AI_GATEWAY_API_KEY')",
        "import io\nname = 'workspace.yaml'",
        "import io as streams\nstreams.open('ordinary.yaml')",
        "import builtins\nbuiltins.open(file='ordinary.yaml')",
    ],
)
def test_source_accepts_declared_downward_imports_and_data(tmp_path: Path, source: str) -> None:
    assert _offenders([_fixture(tmp_path, source)]) == []


@pytest.mark.parametrize(
    "source",
    [
        "import os\nx = os.environ['GRAPH_WORKS_DIR']",
        "import os\nx = os.environ.get('GRAPH_WORKS_DIR')",
        "import os\nx = os.getenv('GRAPH_WORKS_DIR')",
        "import os\nx = os.getenv(key='GRAPH_WORKS_DIR')",
        "import os as operating\nx = operating.getenv(key='GRAPH_WORKS_DIR')",
        "from os import getenv\nx = getenv(key='GRAPH_WORKS_DIR')",
        "from os import getenv as read_env\nx = read_env(key='GRAPH_WORKS_DIR')",
        "import os\nx = os.environ.get(key='GRAPH_WORKS_DIR')",
        "from os import environ as env\nx = env.get(key='GRAPH_WORKS_DIR')",
        "from os import environ\nx = environ['GRAPH_WORKS_DIR']",
        "import io as streams\nx = streams.open('workspace.yaml')",
        "from io import open as reader\nx = reader('workspace.yaml')",
        "from builtins import open\nx = open(file='workspace.yaml')",
        "import os as operating\nx = operating.environ.get(key='GRAPH_WORKS_DIR')",
        "from os import environ\nx = environ.get(key='GRAPH_WORKS_DIR')",
        "import os as operating\nx = operating.getenv('GRAPH_WORKS_DIR')",
        "from os import environ as env\nx = env.get('GRAPH_WORKS_DIR')",
        "from os import getenv as read_env\nx = read_env('GRAPH_WORKS_DIR')",
        "from pathlib import Path\nx = Path('workspace.yaml').read_text()",
        "from pathlib import Path\nmanifest = root / 'workspace.yaml'\nx = manifest.read_bytes()",
        "with open(root / 'workspace.yaml') as stream:\n    text = stream.read()",
    ],
)
def test_source_rejects_direct_discovery(tmp_path: Path, source: str) -> None:
    path = _fixture(tmp_path, source)
    found = _offenders([path])
    assert len(found) == 1
    assert found[0].startswith(f"{path}:")
    assert found[0].endswith("workspace discovery")


@pytest.mark.parametrize("source", ["from gw_kernel.testing import workspace", "from gw_work import commands"])
def test_tests_accept_kernel_fixtures_and_work(tmp_path: Path, source: str) -> None:
    assert _offenders([_fixture(tmp_path, source)], tests=True) == []


@pytest.mark.parametrize(
    "source",
    [
        "import gw_orchestrate",
        "from gw_orchestrate import commands as run",
        "import gw_cli",
        "from gw_serve import routes",
        "from gw_wire import project",
        "from ...gw_orchestrate import commands",
    ],
)
def test_tests_reject_orchestration_and_interfaces(tmp_path: Path, source: str) -> None:
    path = _fixture(tmp_path, source)
    assert _offenders([path], tests=True) == [f"{path}:1 import boundary"]


def test_shipped_source_obeys_wiki_boundaries() -> None:
    paths = sorted(SRC.rglob("*.py"))
    assert len(paths) > 70
    assert _offenders(paths) == []


def test_shipped_tests_obey_wiki_boundaries() -> None:
    paths = sorted((PACKAGE / "tests").rglob("*.py"))
    assert len(paths) > 100
    assert _offenders(paths, tests=True) == []

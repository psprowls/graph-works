"""Pipeline depends on defining kernel/lower APIs; no upward imports or discovery.

AST catches static imports, simple aliases and manifest reads. Arbitrary dynamic
imports/indirection are outside this syntax boundary. Scratch test data authorship
is allowed; transcript_capture is the only production discovery delegation.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest
from gw_kernel.testing.boundaries import discovery_lookups, manifest_reads, module_name, scan_imports, under

PACKAGE = Path(__file__).resolve().parents[1]
SRC = PACKAGE / "src/gw_pipeline"
TESTS = PACKAGE / "tests"
LOWER = frozenset({"work_tracker_okf", "okf_io", "okf_ext", "code_graph_io", "config_io"})
SIBLINGS = frozenset({"gw_work", "gw_orchestrate", "gw_wiki"})
INTERFACES = frozenset({"gw_cli", "gw_serve", "gw_wire"})
DISCOVERY = "gw_kernel.workspace.discovery"
TRANSCRIPT = "gw_pipeline.transcript_capture"
SUPPORT = ("gw_pipeline.testing", "gw_pipeline.test_support", "gw_kernel.testing")


def offenders(source: str, *, module: str, test: bool = False) -> list[str]:
    scan = scan_imports(source, module=module)
    found: list[str] = []
    support = module == "gw_pipeline.testing" or module.startswith("gw_pipeline.test_support.")
    checked = [
        *scan.targets,
        *scan.projections,
        *(name for name in scan.raw if name.split(".")[0] in SIBLINGS | INTERFACES | {"gw_kernel"}),
        *scan.unresolved,
    ]
    for target in checked:
        root = target.split(".")[0]
        if (
            root in SIBLINGS | INTERFACES
            or target == "gw_kernel"
            or root
            not in sys.stdlib_module_names
            | LOWER
            | {"gw_kernel", "gw_pipeline"}
            | ({"pytest"} if test or support else set())
        ):
            found.append(target)
        if not test and not support and any(under(target, name) for name in SUPPORT):
            found.append(target)
    if not test:
        found.extend(finding.text for finding in discovery_lookups(scan.tree))
        # This exemption covers the imported kernel resolver only, never a new
        # environment lookup or direct manifest reader.
        if module != TRANSCRIPT:
            found.extend(target for target in checked if under(target, DISCOVERY))
        found.extend(finding.text for finding in manifest_reads(scan.tree, scan.aliases))
    return found


@pytest.mark.parametrize(
    "source",
    [
        "import gw_work as sibling",
        "import os, gw_orchestrate.commands as commands",
        "from gw_wiki import commands",
        "from gw_work import (commands, archive as a)",
        "if TYPE_CHECKING:\n    import gw_orchestrate",
        "from ..gw_work import commands",
        "from .. import gw_orchestrate",
        "import gw_kernel as kernel",
        "from gw_kernel import (workspace, resolve as r)",
        "import os, gw_kernel as kernel",
        "from ... import gw_kernel",
        "import gw_cli as cli",
        "from gw_serve import routes",
        "from gw_wire import work",
        "import undeclared_external",
    ],
)
@pytest.mark.parametrize("test", [False, True])
def test_rejects_upward_facade_grouped_and_ancestor_imports(source, test):
    assert offenders(source, module="gw_pipeline.guidance.assembly", test=test)


@pytest.mark.parametrize(
    "source",
    [
        "from gw_kernel.workspace import discovery as d",
        "from gw_kernel.workspace.discovery import resolve as r",
        "import gw_kernel.workspace.discovery as d",
        "import gw_kernel.workspace as ws\nws.discovery.resolve()",
        "from gw_kernel.workspace import layout, discovery as d",
        "import os\nos.environ['GRAPH_WORKS_DIR']",
        "from os import getenv as env\nenv('GRAPH_WORKS_DIR')",
        "Path('workspace.yaml').read_text()",
        "open(root / 'workspace.yaml')",
        "from builtins import open as read\nread('workspace.yaml')",
        "manifest = root / 'workspace.yaml'\nmanifest.read_bytes()",
    ],
)
def test_rejects_production_discovery_and_manifest_reads(source):
    assert offenders(source, module="gw_pipeline.finish")


@pytest.mark.parametrize(
    "source",
    [
        "from gw_kernel.workspace.layout import WorkspaceLayout",
        "from gw_kernel.workspace import bundle_lock",
        "from gw_kernel.guidance.closure import affects_closure",
        "from . import transactions",
        "from ..transactions import apply_mutation",
        "from okf_io import parse",
        "import os",
        "# import gw_wiki\ntext = 'workspace.yaml'",
    ],
)
def test_accepts_defining_modules(source):
    assert offenders(source, module="gw_pipeline.guidance.assembly") == []


def test_only_transcript_source_can_delegate_discovery():
    source = "from gw_kernel.workspace.discovery import resolve"
    assert offenders(source, module=TRANSCRIPT) == []
    assert offenders(source, module=TRANSCRIPT + "_extra")
    assert offenders("open('workspace.yaml')", module=TRANSCRIPT)
    assert offenders("import os\nos.getenv('GRAPH_WORKS_DIR')", module=TRANSCRIPT)


def test_scratch_fixture_authorship_and_injected_root_are_allowed():
    source = (
        "from gw_kernel.workspace.init import apply_init, plan_init\n"
        + "env = {'GRAPH_WORKS_DIR': str(tmp_path)}\n"
        + "(tmp_path / 'workspace.yaml').write_text('version: 1')"
    )
    assert offenders(source, module="gw_pipeline.tests.test_hook", test=True) == []


@pytest.mark.parametrize(
    "source",
    [
        "from gw_pipeline.testing import forced_tier",
        "from gw_pipeline import test_support",
        "from gw_pipeline.test_support.finish_repos import workspace",
        "from gw_kernel.testing import workspace",
        "from .test_support import finish_repos as support",
    ],
)
def test_support_is_test_only(source):
    assert offenders(source, module="gw_pipeline.finish")
    assert offenders(source, module="gw_pipeline.tests.example", test=True) == []


def test_shipped_source_and_tests_obey_the_boundary():
    found = []
    for root, test in ((SRC, False), (TESTS, True)):
        paths = sorted(root.rglob("*.py"))
        assert paths, f"empty boundary root: {root}"
        for path in paths:
            module = module_name(path, root, package="gw_pipeline")
            found.extend(
                f"{path}:{target}" for target in offenders(path.read_text(encoding="utf-8"), module=module, test=test)
            )
    assert found == []


def test_pipeline_has_exact_runtime_dependencies_and_no_sibling_dev_edges():
    data = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    expected = {"gw-kernel", *(root.replace("_", "-") for root in LOWER)}
    dependencies = data["project"]["dependencies"]
    assert {entry.split(">=")[0].split("[")[0] for entry in dependencies} == expected
    for group in (
        *data.get("dependency-groups", {}).values(),
        *data["project"].get("optional-dependencies", {}).values(),
    ):
        dependencies = [*dependencies, *(entry for entry in group if isinstance(entry, str))]
    for root in SIBLINGS | INTERFACES:
        distribution = root.replace("_", "-")
        assert all(not entry.startswith(distribution) for entry in dependencies)
        assert distribution not in data.get("tool", {}).get("uv", {}).get("sources", {})


def test_production_packages_do_not_import_migrated_test_support():
    found = []
    for root in sorted(PACKAGE.parent.glob("*/src")):
        for path in sorted(root.rglob("*.py")):
            module = module_name(path, root)
            if module == "gw_pipeline.testing" or module.startswith("gw_pipeline.test_support."):
                continue
            scan = scan_imports(path.read_text(encoding="utf-8"), module=module)
            found.extend(
                f"{path}:{target}"
                for target in scan.targets + scan.projections
                if any(under(target, support) for support in SUPPORT[:2])
            )
    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        "import gw_pipeline as p\np.testing.forced_tier('linux')",
        "import gw_pipeline\ngw_pipeline.test_support.finish_repos.workspace(tmp_path)",
        "import gw_pipeline as pipeline\npipeline.test_support.finish_repos.workspace(tmp_path)",
    ],
)
def test_ancestor_helper_access_is_test_only(source):
    assert offenders(source, module="gw_pipeline.finish")
    assert offenders(source, module="gw_pipeline.tests.example", test=True) == []


@pytest.mark.parametrize(
    "source",
    [
        "import gw_pipeline as p\np.testing.forced_tier('linux')",
        "import gw_pipeline\ngw_pipeline.test_support.finish_repos.workspace(tmp_path)",
        "import gw_pipeline as pipeline\npipeline.test_support.finish_repos.workspace(tmp_path)",
    ],
)
def test_shared_import_scan_resolves_ancestor_helper_access(source):
    scan = scan_imports(source, module="consumer.commands")
    assert any(under(target, support) for target in scan.targets + scan.projections for support in SUPPORT[:2])

"""Pipeline depends on defining kernel/lower APIs; no upward imports or discovery.

AST catches static imports, simple aliases and manifest reads. Arbitrary dynamic
imports/indirection are outside this syntax boundary. Scratch test data authorship
is allowed; transcript_capture is the only production discovery delegation.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from importlib.util import resolve_name
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
SRC = PACKAGE / "src/gw_pipeline"
TESTS = PACKAGE / "tests"
LOWER = frozenset({"work_tracker_okf", "okf_io", "okf_ext", "code_graph_io", "config_io"})
SIBLINGS = frozenset({"gw_work", "gw_orchestrate", "gw_wiki"})
INTERFACES = frozenset({"gw_cli", "gw_serve", "gw_wire"})
DISCOVERY = "gw_kernel.workspace.discovery"
TRANSCRIPT = "gw_pipeline.transcript_capture"
SUPPORT = ("gw_pipeline.testing", "gw_pipeline.test_support", "gw_kernel.testing")


def imports(source: str, *, module: str) -> tuple[ast.Module, list[str], dict[str, str]]:
    tree = ast.parse(source)
    targets: list[str] = []
    aliases: dict[str, str] = {}
    package = module.removesuffix(".__init__") if module.endswith(".__init__") else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if node.level:
                targets.extend(
                    raw
                    for raw in [target, *(alias.name for alias in node.names)]
                    if raw.split(".")[0] in SIBLINGS | INTERFACES | {"gw_kernel"}
                )
                try:
                    target = resolve_name("." * node.level + target, package)
                except ImportError:
                    targets.append(ast.unparse(node))
                    continue
            targets.extend([target, *(f"{target}.{alias.name}" for alias in node.names)])
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{target}.{alias.name}"
    # Include access through imported ancestors in the same target projection
    # used by both package admission and the repository-wide support guard.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = ast.unparse(node).split(".")
            if parts[0] in aliases:
                targets.append(".".join([aliases[parts[0]], *parts[1:]]))
    return tree, targets, aliases


def offenders(source: str, *, module: str, test: bool = False) -> list[str]:
    tree, targets, aliases = imports(source, module=module)
    found: list[str] = []
    support = module == "gw_pipeline.testing" or module.startswith("gw_pipeline.test_support.")
    for target in targets:
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
        if not test and not support and any(target == name or target.startswith(name + ".") for name in SUPPORT):
            found.append(target)
    if not test:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Call, ast.Subscript)) and "GRAPH_WORKS_DIR" in ast.unparse(node):
                found.append(ast.unparse(node))
        # This exemption covers the imported kernel resolver only, never a new
        # environment lookup or direct manifest reader.
        if module != TRANSCRIPT:
            found.extend(target for target in targets if target == DISCOVERY or target.startswith(DISCOVERY + "."))
        found.extend(manifest_reads(tree, aliases))
    return found


def manifest_reads(tree: ast.AST, aliases: dict[str, str]) -> list[str]:
    """Follow simple assignments within lexical scopes, resolving open aliases."""
    found: list[str] = []
    scopes: list[tuple[str, set[str]]] = [("module", set())]

    def is_manifest(expression: ast.AST) -> bool:
        return any(
            (isinstance(node, ast.Constant) and isinstance(node.value, str) and "workspace.yaml" in node.value)
            or (isinstance(node, ast.Name) and node.id in scopes[-1][1])
            for node in ast.walk(expression)
        )

    def bind(target: ast.AST, value: ast.AST | None) -> None:
        paths = scopes[-1][1]
        if isinstance(target, ast.Name):
            if value is not None and is_manifest(value):
                paths.add(target.id)
            else:
                paths.discard(target.id)

    def argument_names(args: ast.arguments) -> set[str]:
        names = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
        names.update(arg.arg for arg in (args.vararg, args.kwarg) if arg is not None)
        return names

    def target_names(target: ast.AST) -> set[str]:
        return {node.id for node in ast.walk(target) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}

    def function_locals(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
        names = argument_names(node.args)
        external: set[str] = set()
        pending: list[ast.AST] = list(node.body)
        while pending:
            child = pending.pop()
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(child.name)
                continue
            if isinstance(child, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                names.add(child.id)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                names.update(alias.asname or alias.name.split(".")[0] for alias in child.names)
            elif isinstance(child, (ast.Global, ast.Nonlocal)):
                external.update(child.names)
            pending.extend(ast.iter_child_nodes(child))
        return names - external

    def manifest_defaults(args: ast.arguments) -> set[str]:
        positional = (*args.posonlyargs, *args.args)
        defaults = [*zip(positional[-len(args.defaults) :], args.defaults, strict=False)]
        defaults.extend(zip(args.kwonlyargs, args.kw_defaults, strict=True))
        return {arg.arg for arg, value in defaults if value is not None and is_manifest(value)}

    class Reads(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            for child in ast.iter_child_nodes(node):
                if child not in node.body:
                    self.visit(child)
            # Defaults use the defining scope (including a class namespace),
            # while a method body closes over the enclosing function/module.
            default_paths = manifest_defaults(node.args)
            inherited = next(paths for kind, paths in reversed(scopes) if kind != "class")
            scopes.append(("function", (inherited - function_locals(node)) | default_paths))
            try:
                for statement in node.body:
                    self.visit(statement)
            finally:
                scopes.pop()
            scopes[-1][1].discard(node.name)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node: ast.Lambda) -> None:
            self.visit(node.args)  # Defaults are evaluated in the enclosing scope.
            default_paths = manifest_defaults(node.args)
            inherited = next(paths for kind, paths in reversed(scopes) if kind != "class")
            scopes.append(("function", (inherited - argument_names(node.args)) | default_paths))
            try:
                self.visit(node.body)
            finally:
                scopes.pop()

        def visit_ListComp(self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp) -> None:
            first = node.generators[0]
            self.visit(first.iter)  # Only the first iterable uses the enclosing scope.
            first_manifest = is_manifest(first.iter)
            locals_ = set().union(*(target_names(generator.target) for generator in node.generators))
            inherited = next(paths for kind, paths in reversed(scopes) if kind != "class")
            scopes.append(("comprehension", inherited - locals_))
            try:
                for index, generator in enumerate(node.generators):
                    if index:
                        self.visit(generator.iter)
                    manifest = is_manifest(generator.iter) if index else first_manifest
                    names = target_names(generator.target)
                    if manifest:
                        scopes[-1][1].update(names)
                    else:
                        scopes[-1][1].difference_update(names)
                    self.visit(generator.target)
                    for condition in generator.ifs:
                        self.visit(condition)
                if isinstance(node, ast.DictComp):
                    self.visit(node.key)
                    self.visit(node.value)
                else:
                    self.visit(node.elt)
            finally:
                scopes.pop()

        visit_SetComp = visit_ListComp
        visit_DictComp = visit_ListComp
        visit_GeneratorExp = visit_ListComp

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            for child in ast.iter_child_nodes(node):
                if child not in node.body:
                    self.visit(child)
            scopes.append(("class", scopes[-1][1].copy()))
            try:
                for statement in node.body:
                    self.visit(statement)
            finally:
                scopes.pop()
            scopes[-1][1].discard(node.name)

        def visit_Assign(self, node: ast.Assign) -> None:
            self.visit(node.value)
            for target in node.targets:
                bind(target, node.value)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if node.value is not None:
                self.visit(node.value)
                bind(node.target, node.value)

        def visit_Call(self, node: ast.Call) -> None:
            parts = ast.unparse(node.func).split(".")
            target = ".".join([aliases.get(parts[0], parts[0]), *parts[1:]])
            if target in {"open", "builtins.open"}:
                filename = (
                    node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "file"), None)
                )
                if filename is not None and is_manifest(filename):
                    found.append(ast.unparse(node))
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"read_text", "read_bytes", "open"}
                and is_manifest(node.func.value)
            ):
                found.append(ast.unparse(node))
            self.generic_visit(node)

    Reads().visit(tree)
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
            module = ".".join(("gw_pipeline", *path.relative_to(root).with_suffix("").parts))
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
            relative = path.relative_to(root).with_suffix("")
            module = ".".join(relative.parts)
            if module == "gw_pipeline.testing" or module.startswith("gw_pipeline.test_support."):
                continue
            _, targets, _ = imports(path.read_text(encoding="utf-8"), module=module)
            found.extend(
                f"{path}:{target}"
                for target in targets
                if any(target == support or target.startswith(support + ".") for support in SUPPORT[:2])
            )
    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\ndef read(manifest=Path('workspace.yaml')):\n    return manifest.read_text()",
        "from pathlib import Path\ndef read(*, manifest=Path('workspace.yaml')):\n    return manifest.read_bytes()",
        "from pathlib import Path\nmanifest = Path('workspace.yaml')\n"
        "def read(path=manifest, /):\n    return path.open()",
        "from pathlib import Path\nasync def read(manifest=Path('workspace.yaml')):\n    return manifest.read_text()",
        "from pathlib import Path\nclass Reader:\n    manifest = Path('workspace.yaml')\n"
        "    def read(self, path=manifest):\n        return path.read_text()",
    ],
)
def test_manifest_function_defaults_are_rejected(source):
    assert offenders(source, module="gw_pipeline.finish")


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\ndef read(manifest=Path('notes.md')):\n    return manifest.read_text()",
        "from pathlib import Path\ndef read(manifest=Path('workspace.yaml')):\n"
        "    manifest = Path('notes.md')\n    return manifest.read_text()",
        "from pathlib import Path\nmanifest = Path('workspace.yaml')\n"
        "def read(manifest=Path('notes.md')):\n    return manifest.read_text()",
    ],
)
def test_manifest_default_aliases_preserve_normal_shadowing(source):
    assert offenders(source, module="gw_pipeline.finish") == []


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
    _, targets, _ = imports(source, module="consumer.commands")
    assert any(target == support or target.startswith(support + ".") for target in targets for support in SUPPORT[:2])

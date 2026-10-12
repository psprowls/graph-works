"""The work slice consumes resolved kernel primitives, never sibling slices."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src/gw_work"
TESTS = Path(__file__).resolve().parent
SIBLINGS = {"gw_orchestrate", "gw_wiki"}
INTERFACES = {"graph_works_cli", "graph_works_serve", "graph_works_wire"}
DISCOVERY = "gw_kernel.workspace.discovery"


def offenders(source: str, *, module: str, test: bool = False) -> list[str]:
    tree = ast.parse(source)
    aliases: dict[str, str] = {}
    found: list[str] = []
    package = module if module.endswith(".__init__") else module.rpartition(".")[0]
    package = package.removesuffix(".__init__")
    for node in ast.walk(tree):
        targets: list[str] = []
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if node.level:
                try:
                    target = resolve_name("." * node.level + target, package)
                except ImportError:
                    found.append(ast.unparse(node))
                    continue
            targets = [target, *(f"{target}.{alias.name}" for alias in node.names)]
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{target}.{alias.name}"
        for target in targets:
            root = target.split(".")[0]
            if root in SIBLINGS or (
                not test
                and (
                    root in INTERFACES
                    or target == "gw_kernel"
                    or target == DISCOVERY
                    or target.startswith(DISCOVERY + ".")
                )
            ):
                found.append(target)
    if not test:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                parts = ast.unparse(node).split(".")
                target = ".".join([aliases.get(parts[0], parts[0]), *parts[1:]])
                if target == DISCOVERY or target.startswith(DISCOVERY + "."):
                    found.append(target)
            if isinstance(node, (ast.Call, ast.Subscript)):
                text = ast.unparse(node)
                if "GRAPH_WORKS_DIR" in text:
                    found.append(text)
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

    class Reads(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            for child in ast.iter_child_nodes(node):
                if child not in node.body:
                    self.visit(child)
            # A method closes over the enclosing function/module, not its class namespace.
            inherited = next(paths for kind, paths in reversed(scopes) if kind != "class")
            scopes.append(("function", inherited - function_locals(node)))
            try:
                for statement in node.body:
                    self.visit(statement)
            finally:
                scopes.pop()
            scopes[-1][1].discard(node.name)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node: ast.Lambda) -> None:
            self.visit(node.args)  # Defaults are evaluated in the enclosing scope.
            positional = (*node.args.posonlyargs, *node.args.args)
            defaults = [*zip(positional[-len(node.args.defaults) :], node.args.defaults, strict=False)]
            defaults.extend(zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True))
            manifest_defaults = {arg.arg for arg, value in defaults if value is not None and is_manifest(value)}
            inherited = next(paths for kind, paths in reversed(scopes) if kind != "class")
            scopes.append(("function", (inherited - argument_names(node.args)) | manifest_defaults))
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
        "import gw_orchestrate as sibling",
        "from gw_wiki import commands",
        "from gw_orchestrate.commands import run",
        "if TYPE_CHECKING:\n    import gw_wiki.commands",
        "from ..gw_orchestrate import commands",
        "import gw_kernel as kernel",
        "from gw_kernel import workspace",
        "import graph_works_wire as wire",
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


@pytest.mark.parametrize(
    "expression,forbidden",
    [
        ('lambda manifest: manifest.read_text(encoding="utf-8")', False),
        ("lambda manifest=manifest.read_text(): manifest", True),
        ("lambda notes: manifest.read_text()", True),
        ('lambda notes: Path("workspace.yaml").read_text()', True),
        ('lambda manifest=Path("notes.md"): manifest.read_text()', False),
        ('lambda manifest=Path("workspace.yaml"): manifest.read_text()', True),
    ],
)
def test_lambda_binding_scope(expression, forbidden):
    source = 'from pathlib import Path\nmanifest = Path("workspace.yaml")\nread_notes = ' + expression
    assert bool(offenders(source, module="gw_work.commands")) is forbidden


COMPREHENSIONS = (
    "[{read} for manifest in {items}]",
    "{{{read} for manifest in {items}}}",
    "{{manifest: {read} for manifest in {items}}}",
    "({read} for manifest in {items})",
)


@pytest.mark.parametrize("template", COMPREHENSIONS)
@pytest.mark.parametrize("scenario", ["notes", "local-manifest", "outer-read", "first-iterable", "restored"])
def test_comprehension_binding_scope(template, scenario):
    outer = "notes.md" if scenario == "local-manifest" else "workspace.yaml"
    items = '[Path("workspace.yaml")]' if scenario == "local-manifest" else '[Path("notes.md")]'
    read = 'manifest.read_text(encoding="utf-8")'
    if scenario == "outer-read":
        template = template.replace("for manifest", "for note")
    elif scenario == "first-iterable":
        items = '[Path(manifest.read_text(encoding="utf-8"))]'
    expression = template.format(read=read, items=items)
    source = f'from pathlib import Path\nmanifest = Path("{outer}")\nnotes = {expression}\n'
    if scenario == "restored":
        source += 'manifest.read_text(encoding="utf-8")\n'
    assert bool(offenders(source, module="gw_work.commands")) is (scenario != "notes")


@pytest.mark.parametrize("template", COMPREHENSIONS)
@pytest.mark.parametrize("forbidden", [False, True])
def test_async_comprehension_binding_scope(template, forbidden):
    read = 'Path("workspace.yaml").read_text()' if forbidden else "manifest.read_text()"
    expression = template.replace("for manifest", "async for manifest").format(read=read, items="paths")
    source = (
        'from pathlib import Path\nmanifest = Path("workspace.yaml")\n'
        f"async def read_notes(paths):\n    return {expression}\n"
    )
    assert bool(offenders(source, module="gw_work.commands")) is forbidden


@pytest.mark.parametrize(
    "body,forbidden",
    [
        ("read_notes = lambda: manifest.read_text()", False),
        ("read_notes = lambda notes=manifest.read_text(): notes", True),
        ('notes = [manifest.read_text() for note in [Path("notes.md")]]', False),
        ("notes = [note for note in manifest.read_text().splitlines()]", True),
    ],
)
def test_class_namespace_is_used_for_defaults_and_first_iterable_only(body, forbidden):
    source = (
        'from pathlib import Path\nmanifest = Path("notes.md")\n'
        f'class Manifest:\n    manifest = Path("workspace.yaml")\n    {body}\n'
    )
    assert bool(offenders(source, module="gw_work.commands")) is forbidden


def test_comprehension_tuple_targets_and_later_iterables_use_local_bindings():
    source = (
        'from pathlib import Path\nmanifest = Path("workspace.yaml")\n'
        'notes = [line for manifest, other in [(Path("notes.md"), 1)]\n'
        "         for line in manifest.read_text().splitlines() if other]\n"
    )
    assert offenders(source, module="gw_work.commands") == []
    assert offenders(source + "manifest.read_text()\n", module="gw_work.commands") == ["manifest.read_text()"]


def test_shipped_source_and_tests_obey_boundary():
    assert SRC.is_dir(), "missing work source root"
    found = []
    for root, test in ((SRC, False), (TESTS, True)):
        for path in sorted(root.rglob("*.py")):
            module = ".".join(("gw_work", *path.relative_to(root).with_suffix("").parts))
            found.extend(
                f"{path}:{target}" for target in offenders(path.read_text(encoding="utf-8"), module=module, test=test)
            )
    assert found == []

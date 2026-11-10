"""Stdlib-only AST mechanism shared by the slice boundary tests.

This module reports facts, never policy: which names each import statement
binds or reaches, which statements read the workspace manifest, and which
expressions look up ``GRAPH_WORKS_DIR``. Each slice's test keeps its own policy
constants and turns these facts into findings. Analysis is static syntax only;
dynamic imports and arbitrary indirection are outside it.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.util import resolve_name
from pathlib import Path

__all__ = [
    "Finding",
    "ImportFact",
    "ImportScan",
    "discovery_lookups",
    "manifest_reads",
    "module_name",
    "scan_imports",
    "under",
]


@dataclass(frozen=True)
class ImportFact:
    """One import statement: resolved targets and raw relative names."""

    line: int
    text: str
    targets: tuple[str, ...]
    raw: tuple[str, ...]
    resolved: bool


@dataclass(frozen=True)
class ImportScan:
    """Everything one source file's imports and attribute projections reach."""

    tree: ast.Module
    imports: tuple[ImportFact, ...]
    aliases: dict[str, str]
    projections: tuple[str, ...]

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(target for fact in self.imports for target in fact.targets)

    @property
    def raw(self) -> tuple[str, ...]:
        return tuple(name for fact in self.imports for name in fact.raw)

    @property
    def unresolved(self) -> tuple[str, ...]:
        return tuple(fact.text for fact in self.imports if not fact.resolved)


@dataclass(frozen=True)
class Finding:
    """One offending node: its line and unparsed text."""

    line: int
    text: str


def scan_imports(source: str, *, module: str) -> ImportScan:
    """Parse ``source`` as ``module`` and report every import's reach."""
    tree = ast.parse(source)
    package = module.removesuffix(".__init__") if module.endswith(".__init__") else module.rpartition(".")[0]
    facts: list[ImportFact] = []
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
            facts.append(
                ImportFact(node.lineno, ast.unparse(node), tuple(alias.name for alias in node.names), (), True)
            )
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            raw: tuple[str, ...] = ()
            if node.level:
                raw = (
                    *((node.module,) if node.module else ()),
                    *(f"{node.module}.{alias.name}" if node.module else alias.name for alias in node.names),
                )
                try:
                    target = resolve_name("." * node.level + target, package)
                except ImportError:
                    facts.append(ImportFact(node.lineno, ast.unparse(node), (), raw, False))
                    continue
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{target}.{alias.name}"
            targets = (target, *(f"{target}.{alias.name}" for alias in node.names))
            facts.append(ImportFact(node.lineno, ast.unparse(node), targets, raw, True))
    projections = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = ast.unparse(node).split(".")
            if parts[0] in aliases:
                projections.append(".".join([aliases[parts[0]], *parts[1:]]))
    return ImportScan(tree, tuple(facts), aliases, tuple(projections))


def discovery_lookups(tree: ast.AST) -> list[Finding]:
    """Every call or subscript whose text mentions ``GRAPH_WORKS_DIR``."""
    return [
        Finding(node.lineno, ast.unparse(node))
        for node in ast.walk(tree)
        if isinstance(node, (ast.Call, ast.Subscript)) and "GRAPH_WORKS_DIR" in ast.unparse(node)
    ]


def module_name(path: Path, root: Path, *, package: str = "") -> str:
    """Dotted module name of ``path`` under ``root``; keeps a trailing ``__init__``."""
    return ".".join([*([package] if package else []), *path.relative_to(root).with_suffix("").parts])


def under(target: str, prefix: str) -> bool:
    """True when ``target`` is ``prefix`` or a dotted descendant of it."""
    return target == prefix or target.startswith(prefix + ".")


def manifest_reads(tree: ast.AST, aliases: Mapping[str, str]) -> list[Finding]:
    """Follow simple assignments within lexical scopes, resolving open aliases."""
    found: list[Finding] = []
    scopes: list[tuple[str, set[str]]] = [("module", set())]

    def is_manifest(expression: ast.AST) -> bool:
        return any(
            (isinstance(node, ast.Constant) and isinstance(node.value, str) and "workspace.yaml" in node.value)
            or (isinstance(node, ast.Name) and node.id in scopes[-1][1])
            for node in ast.walk(expression)
        )

    def bind(target: ast.AST, value: ast.expr | None) -> None:
        paths = scopes[-1][1]
        if isinstance(target, ast.Name):
            if value is not None and is_manifest(value):
                paths.add(target.id)
            else:
                paths.discard(target.id)
            return
        names = tainted(target, [value]) if value is not None and is_manifest(value) else set()
        paths.difference_update(target_names(target))
        paths.update(names)

    def argument_names(args: ast.arguments) -> set[str]:
        names = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
        names.update(arg.arg for arg in (args.vararg, args.kwarg) if arg is not None)
        return names

    def target_names(target: ast.AST) -> set[str]:
        return {node.id for node in ast.walk(target) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}

    def tainted(target: ast.AST, values: list[ast.expr] | None) -> set[str]:
        """Names in ``target`` bound to a manifest; ``values`` are candidate values, ``None`` when opaque."""
        if isinstance(target, ast.Name):
            return {target.id} if values is None or any(is_manifest(value) for value in values) else set()
        if not isinstance(target, (ast.Tuple, ast.List)):
            return target_names(target)
        width = len(target.elts)
        rows = values
        if rows is not None and (
            any(isinstance(element, ast.Starred) for element in target.elts)
            or not all(
                isinstance(row, (ast.Tuple, ast.List))
                and len(row.elts) == width
                and not any(isinstance(element, ast.Starred) for element in row.elts)
                for row in rows
            )
        ):
            rows = None
        names: set[str] = set()
        for position, element in enumerate(target.elts):
            column = (
                None if rows is None else [row.elts[position] for row in rows if isinstance(row, (ast.Tuple, ast.List))]
            )
            names |= tainted(element, column)
        return names

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
        defaults: list[tuple[ast.arg, ast.expr | None]] = [
            *zip(positional[-len(args.defaults) :], args.defaults, strict=False)
        ]
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
                    scopes[-1][1].difference_update(names)
                    if manifest:
                        display = (
                            generator.iter.elts if isinstance(generator.iter, (ast.List, ast.Tuple, ast.Set)) else None
                        )
                        scopes[-1][1].update(tainted(generator.target, display))
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
            if target in {"open", "builtins.open", "io.open"}:
                filename = (
                    node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "file"), None)
                )
                if filename is not None and is_manifest(filename):
                    found.append(Finding(node.lineno, ast.unparse(node)))
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"read_text", "read_bytes", "open"}
                and is_manifest(node.func.value)
            ):
                found.append(Finding(node.lineno, ast.unparse(node)))
            self.generic_visit(node)

    Reads().visit(tree)
    return found

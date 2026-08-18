"""The five reader-level graph callables, and the describe dispatch beneath.

Each callable takes an open `GraphReader` and returns a string, always —
including for failures, which come back as a recoverable `error: …` line
rather than an exception. The consumer is an LLM: a signal it can act on
beats a traceback it cannot.

**No `@tool` decorator and no `langchain_core` import.** C5, the query
vertical, owns that binding — it is the only consumer and it is downstream of
C2, so the decorator sits where the dependency already is. The cost is a thin
factory there, roughly fifteen lines.

Reader lifetime is the caller's: open at command entry, close in `finally`.
That contract is why these take a reader rather than a `GraphTarget`.

`_describe` is the package's single describe dispatch. `commands.graph`
imports it and maps its triple onto a `GraphResult`; `describe` below maps the
same triple onto a string. The reference this ports from implemented the
dispatch twice, over different kind sets, and the two disagreed in ways nobody
chose.
"""

from __future__ import annotations

from code_graph_io import GraphReader, NodeRecord, exit_codes, render

#: Every kind `_describe` renders. `builtin` is last on purpose: it is the
#: fall-through, so there is no `if kind == "builtin"` branch that can never
#: be false.
DESCRIBE_KINDS: tuple[str, ...] = (
    "repository",
    "package",
    "app",
    "path",
    "test_suite",
    "entry_point",
    "dependency",
    "agent_plugin",
    "function",
    "class",
    "method",
    "type",
    "builtin",
)

#: Rows past this are dropped with a truncation notice. An LLM context is the
#: constraint, not the terminal.
ROW_CAP = 50

#: Every DB node kind matches its describe kind 1:1 except `file`, whose
#: describer is `describe_path` (kept name-distinct from the DB's own "path"
#: attribute usage elsewhere).
_INFER_KIND = {"file": "path"}


def _kind_for(match: NodeRecord) -> str:
    return _INFER_KIND.get(match.kind, match.kind)


def _identifier_for(match: NodeRecord) -> str:
    if match.kind == "file":
        return match.path  # type: ignore[return-value]  # file nodes always carry a path
    if match.kind == "dependency":
        return f"{match.attrs.get('ecosystem', 'pypi')}/{match.name}"
    if match.kind == "builtin":
        return f"builtin:{match.path}/{match.name}"
    return match.name


def _describe(
    reader: GraphReader,
    kind: str | None,
    identifier: str | None,
    depth: int | None = None,
    *,
    in_package: str | None = None,
    fmt: str = "human",
) -> tuple[int, str, str]:
    """Describe one entity: `(exit_code, output, error)`.

    Never raises. `repository` takes no identifier; every other kind requires
    one. An ambiguous bare entry-point name comes back as `AMBIGUOUS` naming
    the candidate packages — the difference between a caller retrying
    correctly and a caller giving up.

    When `kind` is `None`, infers it from `identifier` via `resolve_selector`.
    A single match dispatches as if that kind had been passed explicitly; more
    than one match returns a disambiguation menu; zero matches falls back to
    `kind="path"`.
    """
    if kind is None:
        if identifier is None:
            kind = "repository"
        elif identifier.startswith("builtin:"):
            kind = "builtin"
        else:
            matches = reader.resolve_selector(selector=identifier, in_package=in_package)
            if not matches:
                kind = "path"
            elif len(matches) == 1:
                kind, identifier = _kind_for(matches[0]), _identifier_for(matches[0])
            else:
                return exit_codes.SUCCESS, render.format_matches(reader.build_menu(matches), fmt), ""

    if kind not in DESCRIBE_KINDS:
        return exit_codes.GENERIC, "", f"error: invalid kind '{kind}'; valid: {', '.join(DESCRIBE_KINDS)}"

    if kind == "repository":
        repo = reader.describe_repository()
        if repo is None:
            return exit_codes.GENERIC, "", "error: repository not found"
        children, eff = reader.children_for(kind="repository", name=repo.name, depth=depth)
        return exit_codes.SUCCESS, render.format_repo(repo, fmt, children=children, effective_depth=eff), ""

    if identifier is None:
        return exit_codes.GENERIC, "", f"error: identifier required for kind '{kind}'"

    if kind == "package":
        package = reader.describe_package(name=identifier)
        if package is None:
            return exit_codes.GENERIC, "", f"error: package not found: {identifier}"
        children, eff = reader.children_for(kind="package", name=package.name, depth=depth)
        return exit_codes.SUCCESS, render.format_package(package, fmt, children=children, effective_depth=eff), ""

    if kind == "app":
        app = reader.describe_app(name=identifier)
        if app is None:
            return exit_codes.GENERIC, "", f"error: app not found: {identifier}"
        children, eff = reader.children_for(kind="app", name=app.name, depth=depth)
        return exit_codes.SUCCESS, render.format_app(app, fmt, children=children, effective_depth=eff), ""

    if kind == "path":
        file = reader.describe_path(path=identifier)
        if file is None:
            return exit_codes.GENERIC, "", f"error: path not found in graph: {identifier}"
        children, eff = reader.children_for(kind="file", path=file.path, depth=depth)
        return exit_codes.SUCCESS, render.format_path(file, fmt, children=children, effective_depth=eff), ""

    if kind == "test_suite":
        suite = reader.describe_test_suite(suite_name=identifier)
        if suite is None:
            return exit_codes.GENERIC, "", f"error: test suite not found: {identifier}"
        children, eff = reader.children_for(kind="test_suite", name=suite.name, depth=depth)
        return exit_codes.SUCCESS, render.format_suite(suite, fmt, children=children, effective_depth=eff), ""

    if kind == "entry_point":
        entry, ambiguous = reader.resolve_entry_point(identifier)
        if ambiguous:
            return (
                exit_codes.AMBIGUOUS,
                "",
                f"error: entry point not found: {identifier} "
                f"(ambiguous across packages: {', '.join(ambiguous)}; use 'package:entry')",
            )
        if entry is None:
            return exit_codes.GENERIC, "", f"error: entry point not found: {identifier}"
        return exit_codes.SUCCESS, render.format_entry_point(entry, fmt), ""

    if kind == "dependency":
        # No ecosystem argument reaches here, so the `ecosystem/name` prefix
        # carries it and a bare name means pypi.
        if "/" in identifier:
            ecosystem, _, dep_name = identifier.partition("/")
        else:
            ecosystem, dep_name = "pypi", identifier
        dependency = reader.describe_dependency(ecosystem=ecosystem, name=dep_name)
        if dependency is None:
            return exit_codes.GENERIC, "", f"error: dependency not found: {identifier}"
        return exit_codes.SUCCESS, render.format_dependency(dependency, fmt), ""

    if kind == "agent_plugin":
        plugin = reader.describe_agent_plugin(name=identifier)
        if plugin is None:
            return exit_codes.GENERIC, "", f"error: agent_plugin not found: {identifier}"
        return exit_codes.SUCCESS, render.format_agent_plugin(plugin, fmt), ""

    if kind in ("function", "class", "method", "type"):
        symbol = reader.describe_symbol(kind=kind, name=identifier, in_package=in_package)
        if symbol is None:
            return exit_codes.GENERIC, "", f"error: {kind} not found: {identifier}"
        children, eff = reader.children_for(
            kind=symbol.kind, name=symbol.name, path=symbol.path, line=symbol.line, depth=depth
        )
        return exit_codes.SUCCESS, render.format_symbol(symbol, fmt, children=children, effective_depth=eff), ""

    # builtin — the last member of DESCRIBE_KINDS, reached by fall-through.
    rest = identifier.removeprefix("builtin:")
    if "/" not in rest:
        return exit_codes.GENERIC, "", f"error: malformed builtin URI: {identifier}"
    language, module_name = rest.split("/", 1)
    builtin = reader.describe_builtin(language=language, module_name=module_name)
    if builtin is None:
        return exit_codes.GENERIC, "", f"error: builtin not found: {identifier}"
    return exit_codes.SUCCESS, render.format_builtin(builtin, fmt), ""


def describe(reader: GraphReader, *, kind: str, identifier: str, fmt: str = "human") -> str:
    """Describe one entity, or the `error: …` line saying why not.

    `identifier` is required by the signature and ignored when
    `kind == "repository"`, which keeps every call site one shape.
    """
    exit_code, output, error = _describe(reader, kind, identifier, fmt=fmt)
    return output if exit_code == exit_codes.SUCCESS else error


def find(
    reader: GraphReader,
    *,
    name: str | None = None,
    kind: str | None = None,
    in_package: str | None = None,
    fmt: str = "human",
) -> str:
    """Find nodes by name, kind and/or containing package. Filters AND-combine.

    Zero rows renders as the empty string — a question with no answer is not
    a failure. Calling with no filter at all is rejected here, before the
    reader is asked. An unknown `kind` is the reader's own `ValueError`,
    surfaced as an `error: …` line instead of raised.
    """
    if name is None and kind is None and in_package is None:
        return "error: at least one of name, kind, in_package required"
    try:
        rows = reader.find(name=name, kind=kind, in_package=in_package)
    except ValueError as exc:
        return f"error: {exc}"
    return render.render(rows, fmt, cap=ROW_CAP)


def callers(reader: GraphReader, *, name: str, depth: int = 3) -> str:
    """Callers of *name*, up to *depth* levels out."""
    return render.render(reader.callers(name=name, depth=depth), "human", cap=ROW_CAP)


def callees(reader: GraphReader, *, name: str, depth: int = 3) -> str:
    """Callees of *name*, up to *depth* levels in."""
    return render.render(reader.callees(name=name, depth=depth), "human", cap=ROW_CAP)


def imports(reader: GraphReader, *, path: str) -> str:
    """Modules imported by the file at *path* (repo-relative)."""
    return render.render(reader.imports(path=path), "human", cap=ROW_CAP)


__all__ = ["DESCRIBE_KINDS", "ROW_CAP", "callees", "callers", "describe", "find", "imports"]

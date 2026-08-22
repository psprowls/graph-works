"""`gw graph` — build, describe, find and export the code graph.

Four commands over `graph_works_core.graph.commands`, which already returns a
`GraphResult` carrying a stable exit code and two rendered strings. This module
resolves `--workspace` into a `GraphTarget`, calls the matching core function,
routes `output`/`error` to stdout/stderr, and exits `result.exit_code`. There is
no behavior here beyond routing (ADR-0013 rule 5).

The one thing it does not pass through verbatim is the error *prefix*. Core
spells its failures `error: <exc>`; every other `gw` sub-app writes
`Error: <exc>`. D-026 settles that on the capitalized form — the text is part of
the command surface C6 freezes, and after the freeze changing it is breaking.
`_emit` restyles the prefix and nothing else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Never

import typer
from code_wiki_okf.config import ConfigError
from graph_works_core.graph.commands import GraphResult, GraphTarget, graph_target
from graph_works_core.graph.commands import build as core_build
from graph_works_core.graph.commands import describe as core_describe
from graph_works_core.graph.commands import export as core_export
from graph_works_core.graph.commands import find as core_find

from graph_works_cli import exit_codes
from graph_works_cli.workspace_resolution import resolve_workspace

graph_app = typer.Typer(name="graph", help="Code-graph queries.", no_args_is_help=True)

#: Core's own failure prefix, restyled onto the CLI's `Error:` by `_emit` (D-026).
_CORE_ERROR_PREFIX = "error: "


def _normalize_error(message: str) -> str:
    """Restyle core's lowercase `error:` prefix onto the CLI's `Error:` (D-026)."""
    if message.startswith(_CORE_ERROR_PREFIX):
        return f"Error: {message[len(_CORE_ERROR_PREFIX) :]}"
    return message


def _emit(result: GraphResult) -> None:
    """Route a result's two strings: `output` to stdout, `error` to stderr, never mixed."""
    if result.output:
        typer.echo(result.output)
    if result.error:
        typer.echo(_normalize_error(result.error), err=True)


def _run(result: GraphResult) -> Never:
    """Emit *result* and end the current command with its exit code."""
    _emit(result)
    raise typer.Exit(code=result.exit_code)


def _exit_usage_error(message: str) -> Never:
    """Report a CLI-band usage error with Click's usage exit code."""
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=2)


def _resolve_target(workspace: str) -> GraphTarget:
    """`--workspace` -> `WorkspaceLayout` -> `GraphTarget`, or exit saying why not."""
    layout = resolve_workspace(workspace)
    try:
        return graph_target(layout)
    except ConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=exit_codes.GENERIC) from exc


@graph_app.command("build")
def build(
    full: bool = typer.Option(False, "--full", help="Rebuild from scratch instead of refreshing."),
    only: str = typer.Option("", "--only", help="Build one member by its `workspace.yaml` `repositories` key."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root."),
) -> None:
    """Build or refresh the code graph from the workspace's declared repositories."""
    target = _resolve_target(workspace)
    _run(core_build(target, full=full, only=only or None))


@graph_app.command("describe")
def describe(
    selector: str = typer.Argument("", help="What to describe. Omit to describe the repository."),
    kind: str = typer.Option("", "--kind", help="Entity kind. Omit to infer it from SELECTOR."),
    in_package: str = typer.Option("", "--in-package", help="Narrow inference to one package or app."),
    depth: int | None = typer.Option(None, "--depth", help="Containment-tree depth."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root."),
) -> None:
    """Describe one graph entity, inferring its kind from SELECTOR when --kind is omitted."""
    target = _resolve_target(workspace)
    _run(
        core_describe(
            target,
            kind=kind or None,
            identifier=selector or None,
            depth=depth,
            in_package=in_package or None,
            fmt="json" if json_output else "human",
        )
    )


@graph_app.command("find")
def find(
    name: str = typer.Option("", "--name", help="Match nodes by name."),
    kind: str = typer.Option("", "--kind", help="Match nodes by kind."),
    in_package: str = typer.Option("", "--in-package", help="Match nodes inside one package or app."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root."),
) -> None:
    """Find graph nodes by name, kind and/or containing package."""
    if not (name or kind or in_package):
        _exit_usage_error("at least one of --name, --kind, --in-package required")
    target = _resolve_target(workspace)
    _run(
        core_find(
            target,
            name=name or None,
            kind=kind or None,
            in_package=in_package or None,
            fmt="json" if json_output else "human",
        )
    )


@graph_app.command("export")
def export(
    out: str = typer.Option("", "--out", help="Write GraphML here; omit or `-` to write to stdout."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root."),
) -> None:
    """Export the whole graph as GraphML."""
    target = _resolve_target(workspace)
    _run(core_export(target, out=None if out in ("", "-") else Path(out)))

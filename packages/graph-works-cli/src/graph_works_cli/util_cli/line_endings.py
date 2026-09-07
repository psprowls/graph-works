"""`gw util line-endings` -- detect (and, with `--fix`, repair) CRLF
reaccumulation in the bundle.

`.gitattributes` declares every tracked bundle member LF-in-worktree, but git
enforces that only at checkout; nothing enforces it on write, and `git
status` cannot see a violation because it compares normalised content. This
is the re-runnable detector for that gap -- the bare verb *is* the detection
surface (no lint-catalog rule, no `okf-io` extra rule ships alongside it).
"""

from __future__ import annotations

import json
from typing import Any

import typer
from graph_works_core.util.commands import run_line_endings

from graph_works_cli import exit_codes
from graph_works_cli.errors import exit_error
from graph_works_cli.workspace_resolution import resolve_workspace


def line_endings(
    fix: bool = typer.Option(False, "--fix", help="Rewrite every CRLF member to LF."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root; defaults to discovery."),
    json_output: bool = typer.Option(False, "--json", help="Emit every finding in full as JSON."),
) -> None:
    """Report (or repair) bundle members whose on-disk bytes contain CRLF."""
    layout = resolve_workspace(workspace)
    try:
        report = run_line_endings(layout, fix=fix)
    except OSError as exc:
        exit_error(str(exc), cause=exc)

    if json_output:
        payload: dict[str, Any] = {
            "fixed": report.fixed,
            "findings": [{"member": finding.member, "crlf_count": finding.crlf_count} for finding in report.findings],
        }
        typer.echo(json.dumps(payload, indent=2))
    elif not report.findings:
        typer.echo("clean: no CRLF found")
    else:
        verb = "fixed" if report.fixed else "found"
        lines = [f"{verb}: {len(report.findings)}"]
        lines.extend(f"  {finding.member} ({finding.crlf_count})" for finding in report.findings)
        typer.echo("\n".join(lines))

    if report.findings and not report.fixed:
        raise typer.Exit(code=exit_codes.GENERIC)

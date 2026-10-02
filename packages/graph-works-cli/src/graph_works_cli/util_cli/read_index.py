"""`gw util read-index` — format core diagnostics and map maintenance exits."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import typer
from graph_works_core.util.read_index import ReadIndexReport, run_read_index
from graph_works_wire.util import read_index_payload

from graph_works_cli import exit_codes
from graph_works_cli.errors import exit_error
from graph_works_cli.json_output import encode
from graph_works_cli.workspace_resolution import resolve_workspace


def _human(report: ReadIndexReport) -> str:
    missing = " (missing)" if not report.exists else ""
    reason = f" ({report.backend_reason})" if report.backend_reason is not None else ""
    reconciled = "none"
    if report.last_reconcile_ns is not None:
        seconds, nanoseconds = divmod(report.last_reconcile_ns, 1_000_000_000)
        reconciled = (
            datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds, microseconds=nanoseconds // 1000)
        ).isoformat()
    lines = [
        f"database: {report.db_path}{missing}",
        f"enabled: {str(report.enabled).lower()}",
        f"backend: {report.backend}{reason}",
        f"generation: {report.generation}",
        f"last reconcile: {reconciled}",
        f"schema: {report.schema_version}",
        f"projection: {report.projection_version}",
        f"okf_io: {report.okf_io_version}",
        "fingerprint: " + ("current" if report.current_fingerprint == report.stored_fingerprint else "stale"),
    ]
    lines.extend(f"table {name}: {count}" for name, count in sorted(report.tables.items()))
    lines.extend(f"kind {name}: {count}" for name, count in sorted(report.kinds.items()))
    lines.append(f"unreadable files: {report.unreadable_files}")
    if report.rebuilt_reason is not None:
        lines.append(f"rebuilt: {report.rebuilt_reason}")
    if report.elapsed_s is not None:
        lines.append(f"rebuild: {report.elapsed_s}s, {report.parsed} files parsed")
    lines.append("drift:" if report.drift else "drift: none")
    if report.drift:
        lines.extend(f"  {member}" for member in report.drift)
    if report.error is not None:
        lines.append(f"error: {report.error}")
    return "\n".join(lines)


def read_index(
    verify: bool = typer.Option(
        False, "--verify", help="Reconcile, then re-hash every stored file; exit nonzero on drift."
    ),
    rebuild: bool = typer.Option(False, "--rebuild", help="Delete the database and rebuild it."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root; defaults to discovery."),
    json_output: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
) -> None:
    """Inspect, verify or rebuild the persisted read index."""
    layout = resolve_workspace(workspace, json_mode=json_output, command="util read-index")
    try:
        report = run_read_index(layout, verify=verify, rebuild=rebuild)
    except ValueError as exc:
        exit_error(str(exc), code=exit_codes.GENERIC, cause=exc)
    except (OSError, sqlite3.Error) as exc:
        exit_error(str(exc), cause=exc)
    typer.echo(encode(read_index_payload(report)) if json_output else _human(report))
    if report.drift:
        raise typer.Exit(exit_codes.STALE)

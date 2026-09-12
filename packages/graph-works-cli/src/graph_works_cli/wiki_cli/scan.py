"""The root-level scan command and its process-boundary modes."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

import typer
from graph_works_core.scan.commands import (
    BRIEFS_DIRNAME,
    WORKLIST_FILENAME,
    apply_scan_worklist,
    build_scan_worklist,
    emit_scan_worklist,
    load_worklist,
    run_scan,
    scan_cache_dir,
    scan_results_dir,
)
from graph_works_core.scan.scan_contract import UnsupportedWorklistSchema
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.errors import ScanError

from graph_works_cli import exit_codes
from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.wiki_cli.rendering import scan_apply_payload, scan_emit_payload, scan_normal_payload
from graph_works_cli.workspace_resolution import resolve_workspace


def _exit_usage_error(message: str) -> Never:
    """Report a conditional option requirement with Click's usage exit code."""
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=2)


def _reset_results_dir(path: Path) -> None:
    """Empty the one canonical results directory without following links."""
    if path.is_symlink():
        path.unlink()
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _emit_json(payload: dict[str, object]) -> None:
    """Write a process-boundary payload with no incidental stdout."""
    typer.echo(json.dumps(payload, indent=2))


def scan(
    no_narrate: bool = typer.Option(False, "--no-narrate"),
    emit_worklist: bool = typer.Option(False, "--emit-worklist"),
    apply: bool = typer.Option(False, "--apply"),
    results_dir: str = typer.Option("", "--results-dir"),
    short_head: str = typer.Option("", "--short-head"),
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Run scan locally, emit its worklist, or apply an external result directory."""
    if emit_worklist and apply:
        _exit_usage_error("--emit-worklist and --apply are mutually exclusive")
    if no_narrate and (emit_worklist or apply):
        _exit_usage_error("--no-narrate is a normal-mode flag and is mutually exclusive with emit/apply")
    if apply and (not results_dir or not short_head):
        _exit_usage_error("--apply requires both --results-dir and --short-head")
    if not apply and results_dir:
        _exit_usage_error("--results-dir is only valid with --apply")
    if not apply and short_head:
        _exit_usage_error("--short-head is only valid with --apply")
    if (emit_worklist or apply) and json_output:
        _exit_usage_error("--json is only valid in normal mode")

    layout = resolve_workspace(workspace)
    now = datetime.now(UTC)

    if apply:
        worklist_path = scan_cache_dir(layout) / WORKLIST_FILENAME
        try:
            worklist = load_worklist(worklist_path)
        except UnsupportedWorklistSchema as exc:
            exit_error(str(exc), code=exit_codes.SCHEMA_MISMATCH, cause=exc)
        except (OSError, ValueError) as exc:
            exit_error(str(exc), cause=exc)
        if short_head != worklist.short_head:
            exit_error(
                f"supplied short head {short_head!r} does not match worklist {worklist.short_head!r}",
                code=exit_codes.STALE,
            )
        try:
            config = load_workspace_config(layout)
            applied = apply_scan_worklist(
                worklist_path=worklist_path,
                worklist=worklist,
                results_dir=Path(results_dir).expanduser().resolve(),
                bundle_root=layout.bundle_dir,
                config=config,
                today=now.date(),
                dry_run=False,
            )
        except (OSError, ScanError, ValueError) as exc:
            exit_error(str(exc), cause=exc)
        _emit_json(scan_apply_payload(applied))
        if not applied.ok:
            exit_error("scan apply completed with entity errors")
        return

    try:
        config = load_workspace_config(layout)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    try:
        if emit_worklist:
            worklist, structural = asyncio.run(
                build_scan_worklist(layout, config, today=now.date(), at=now, dry_run=False)
            )
            cache_dir = scan_cache_dir(layout)
            emit_scan_worklist(worklist, out_dir=cache_dir)
            canonical_results_dir = scan_results_dir(layout)
            _reset_results_dir(canonical_results_dir)
            _emit_json(
                scan_emit_payload(
                    worklist_path=cache_dir / WORKLIST_FILENAME,
                    briefs_dir=cache_dir / BRIEFS_DIRNAME,
                    results_dir=canonical_results_dir,
                    short_head=worklist.short_head,
                    structural=structural,
                )
            )
            # Both lanes, not just the entity one: a mirror repo that failed
            # mid-write must exit non-zero here exactly as an index refusal does.
            if structural.errors:
                exit_error("scan emitted entity errors")
            return

        result = asyncio.run(run_scan(layout, config, today=now.date(), at=now, narrate=not no_narrate, dry_run=False))
    except (OSError, ScanError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    if json_output:
        _emit_json(scan_normal_payload(result))
    if not result.ok:
        exit_error("scan completed with entity errors")

"""The root-level scan command and its process-boundary modes."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from gw_kernel.workspace.config import load_workspace_config
from gw_kernel.workspace.errors import ScanError
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_wiki.scan.commands import (
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
from gw_wiki.scan.repo_scan import run_repo_scan
from gw_wiki.scan.scan_contract import UnsupportedWorklistSchema
from gw_wire.wiki import repo_scan_payload, scan_apply_payload, scan_emit_payload, scan_normal_payload
from gw_wire.work import regen_index_payload
from gw_work import run_regen_indexes

from gw_cli import exit_codes
from gw_cli.json_output import encode
from gw_cli.wiki_cli.errors import exit_error
from gw_cli.workspace_resolution import resolve_workspace


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
    typer.echo(encode(payload))


def _reconcile_work_indexes(layout: WorkspaceLayout) -> dict[str, Any]:
    """Reconcile the work-lane indexes a successful full scan leaves behind."""
    plan = regen_index_payload(run_regen_indexes(layout, dry_run=True))
    if not plan["indexes"] or plan["refusals"]:
        # Nothing to write (or nothing safe to write): no apply, so no commit either.
        return plan
    return regen_index_payload(run_regen_indexes(layout, dry_run=False))


def _report_work_indexes(work_indexes: dict[str, Any], *, json_output: bool) -> None:
    """Print the reconciliation for text mode, then fail on a refusal or incomplete apply."""
    for warning in work_indexes["warnings"]:
        typer.echo(f"Warning: {warning}", err=True)
    if not json_output:
        for index in work_indexes["indexes"]:
            typer.echo(f"reconciled {index}")
    if work_indexes["refusals"]:
        exit_error("work index reconciliation refused")
    if work_indexes["applied"] and (work_indexes["rolled_back"] or work_indexes["failures"]):
        exit_error("work index reconciliation incomplete")


def scan(
    no_narrate: bool = typer.Option(False, "--no-narrate"),
    emit_worklist: bool = typer.Option(False, "--emit-worklist"),
    apply: bool = typer.Option(False, "--apply"),
    results_dir: str = typer.Option("", "--results-dir"),
    short_head: str = typer.Option("", "--short-head"),
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
    repo: str = typer.Option(
        "", "--repo", help="Structurally scan only this declared repository (needs --no-narrate)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="With --repo: plan the scan and write nothing."),
) -> None:
    """Run scan locally, emit its worklist, or apply an external result directory."""
    # Click's usage code below is deliberately not exit_codes.STALE.
    if repo and (not no_narrate or emit_worklist or apply or results_dir):
        exit_error(
            "--repo runs a structural scan: pass --no-narrate, and no --emit-worklist/--apply/--results-dir", code=2
        )
    if dry_run and not repo:
        exit_error("--dry-run is only supported with --repo", code=2)
    if emit_worklist and apply:
        exit_error("--emit-worklist and --apply are mutually exclusive", code=2)
    if no_narrate and (emit_worklist or apply):
        exit_error("--no-narrate is a normal-mode flag and is mutually exclusive with emit/apply", code=2)
    if apply and (not results_dir or not short_head):
        exit_error("--apply requires both --results-dir and --short-head", code=2)
    if not apply and results_dir:
        exit_error("--results-dir is only valid with --apply", code=2)
    if not apply and short_head:
        exit_error("--short-head is only valid with --apply", code=2)
    if (emit_worklist or apply) and json_output:
        exit_error("--json is only valid in normal mode", code=2)

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

    if repo:
        try:
            run = run_repo_scan(layout, config, repo=repo, at=now, today=now.date(), dry_run=dry_run)
        except (OSError, ScanError, ValueError) as exc:
            exit_error(str(exc), cause=exc)
        if json_output:
            _emit_json(repo_scan_payload(run))
        else:
            for warning in run.structural.warnings:
                typer.echo(f"Warning: {warning}", err=True)
        if run.refusal is not None:
            exit_error(f"scan --repo {repo}: {run.refusal}: {run.detail}")
        if run.structural.errors:
            exit_error(f"scan --repo {repo}: {'incomplete' if run.applied else 'plan has structural errors'}")
        return

    try:
        if emit_worklist:
            worklist, structural = asyncio.run(
                build_scan_worklist(layout, config, today=now.date(), at=now, dry_run=False)
            )
            cache_dir = scan_cache_dir(layout)
            emit_scan_worklist(worklist, out_dir=cache_dir)
            canonical_results_dir = scan_results_dir(layout)
            _reset_results_dir(canonical_results_dir)
            emit_work_indexes = None if structural.errors else _reconcile_work_indexes(layout)
            _emit_json(
                scan_emit_payload(
                    worklist_path=cache_dir / WORKLIST_FILENAME,
                    briefs_dir=cache_dir / BRIEFS_DIRNAME,
                    results_dir=canonical_results_dir,
                    short_head=worklist.short_head,
                    structural=structural,
                    work_indexes=emit_work_indexes,
                )
            )
            # Both lanes, not just the entity one: a mirror repo that failed
            # mid-write must exit non-zero here exactly as an index refusal does.
            if structural.errors:
                exit_error("scan emitted entity errors")
            if emit_work_indexes is not None:
                _report_work_indexes(emit_work_indexes, json_output=True)
            return

        result = asyncio.run(run_scan(layout, config, today=now.date(), at=now, narrate=not no_narrate, dry_run=False))
        work_indexes = _reconcile_work_indexes(layout) if result.ok else None
    except (OSError, ScanError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    if json_output:
        _emit_json(scan_normal_payload(result, work_indexes=work_indexes))
    else:
        for warning in result.structural.warnings:
            typer.echo(f"Warning: {warning}", err=True)
    if not result.ok:
        exit_error("scan completed with entity errors")
    if work_indexes is not None:
        _report_work_indexes(work_indexes, json_output=json_output)

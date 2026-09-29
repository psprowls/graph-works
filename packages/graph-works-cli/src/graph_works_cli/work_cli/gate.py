"""`gw work gate` -- run, wait for, and check the repository gate for a work item.

Interface band only: parse arguments, resolve the workspace, call **one** core
verb, project it with the wire, choose the exit code. The clock and the run
token are read here and nowhere below.

Exit codes: `run` 0 started/running/satisfied, 3 refusal; `wait` 0 green,
1 red, 2 still running, 3 orphaned or refusal; `check` 0 satisfied,
1 unsatisfied, 3 refusal.
"""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NoReturn

import typer
from graph_works_core.orchestrate.gate import run_gate_check, run_gate_run, run_gate_wait, spawn_runner
from graph_works_core.orchestrate.wait import WaitClock
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_wire import work as wire_work

from graph_works_cli import exit_codes
from graph_works_cli.work_cli import rendering
from graph_works_cli.workspace_resolution import resolve_workspace

gate_app = typer.Typer(name="gate", help="Run and check the repository gate for a work item.", no_args_is_help=True)

REFUSED = 3
DEFAULT_WAIT_TIMEOUT = 540.0

_PATH = typer.Argument(..., help="Extensionless bundle-relative canonical concept path.")


def _refuse(path: str, payload: dict[str, Any]) -> NoReturn:
    refusal = payload["refusal"]
    rendering.fail(
        f"{path}: refused ({refusal['reason']}) -- {refusal['detail']}",
        reason="refused",
        code=REFUSED,
        payload=payload,
    )


def _worktree(value: str) -> Path | None:
    return Path(value) if value else None


@gate_app.command("run")
def gate_run(
    path: str = _PATH,
    scope: str = typer.Option("full", "--scope", help="Gate scope: full or scoped."),
    worktree: str = typer.Option("", "--worktree", help="Worktree path; default is the item's recorded worktree."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the run result as JSON."),
) -> None:
    """Start (or join, or short-circuit) the gate run for PATH's worktree tree."""
    if scope not in ("full", "scoped"):
        rendering.fail(f"--scope must be full or scoped, not {scope!r}", reason="usage", code=2)
    layout = resolve_workspace(workspace)
    chosen: Literal["full", "scoped"] = "scoped" if scope == "scoped" else "full"
    try:
        result = run_gate_run(
            layout,
            path,
            scope=chosen,
            worktree=_worktree(worktree),
            now=datetime.now(UTC),
            token=secrets.token_hex(4),
            spawn=spawn_runner,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    payload = wire_work.gate_run_payload(result, path)
    if payload["refusal"] is not None:
        _refuse(path, payload)
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_gate_run(payload)


@gate_app.command("wait")
def gate_wait(
    path: str = _PATH,
    run_id: str = typer.Option("", "--run", help="Run id to wait for; default is the item's latest run."),
    timeout: float = typer.Option(DEFAULT_WAIT_TIMEOUT, "--timeout", help="Seconds to wait before reporting running."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the wait result as JSON."),
) -> None:
    """Report a gate run's outcome, waiting up to --timeout seconds. Never starts a run."""
    layout = resolve_workspace(workspace)
    clock = WaitClock(wall=lambda: datetime.now(UTC), monotonic=time.monotonic)
    try:
        result = run_gate_wait(
            layout,
            path,
            run_id=run_id or None,
            timeout=timeout,
            clock=clock,
            sleep=time.sleep,
            today=datetime.now(UTC).date(),
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    payload = wire_work.gate_wait_payload(result, path)
    if payload["refusal"] is not None:
        _refuse(path, payload)
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_gate_wait(payload)
    if result.status == "finished":
        raise typer.Exit(0 if result.exit == 0 else 1)
    raise typer.Exit(2 if result.status == "running" else REFUSED)


@gate_app.command("check")
def gate_check(
    path: str = _PATH,
    worktree: str = typer.Option("", "--worktree", help="Worktree path; default is the item's recorded worktree."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the check result as JSON."),
) -> None:
    """Whether PATH's worktree tree already has a satisfying gate receipt. Writes nothing."""
    layout = resolve_workspace(workspace)
    try:
        result = run_gate_check(layout, path, worktree=_worktree(worktree))
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    payload = wire_work.gate_check_payload(result, path)
    if payload["refusal"] is not None:
        _refuse(path, payload)
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_gate_check(payload)
    raise typer.Exit(0 if result.status == "satisfied" else 1)

"""`gw work obligation` records finish obligations on work items."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast

import typer
from graph_works_core.work.obligations import run_obligation_add
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_wire import work as wire_work

from graph_works_cli import exit_codes
from graph_works_cli.work_cli import rendering
from graph_works_cli.workspace_resolution import resolve_workspace

obligation_app = typer.Typer(name="obligation", help="Finish obligations.", no_args_is_help=True)


def _today() -> date:
    return datetime.now(UTC).date()


@obligation_app.command()
def add(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    text: str = typer.Option(..., "--text", help="The deferred step, one line."),
    apply: bool = typer.Option(False, "--apply", help="Write and commit; the default only plans."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option(""),
) -> None:
    """Record a plan step deferred to finish as a finish obligation."""
    layout = resolve_workspace(workspace)
    try:
        result = run_obligation_add(layout, path, text=text, on=_today(), dry_run=not apply)
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    payload = wire_work.obligation_payload(result)
    for warning in payload["warnings"]:
        rendering.warn(str(warning))
    if payload["refusal"] is not None:
        rendering.fail(
            f"refused ({payload['refusal']['reason']}); nothing was applied", reason="refused", payload=payload
        )
    if payload["applied"] and (payload["rolled_back"] or payload["failures"]):
        rendering.fail("obligation apply was incomplete", reason="incomplete-apply", payload=payload)
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_obligations(payload)
        rendering.render_commit(cast(dict[str, Any] | None, payload["commit"]))

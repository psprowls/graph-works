"""`gw work decision` — the decisions ledger, nested under `gw work`.

Every verb accepts any canonical path and core resolves its nearest
Release/Epic/Feature decision owner, else the item itself, so a fan-out
worker need not know it.

`overturn` is one logical operation, not two commands: it retires the old
decision, records its replacement, and files the follow-up work item
describing remediation for what already landed. Core preflights both halves
before either write, so an expected refusal leaves both untouched.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import typer
from graph_works_core.work import commands as work
from graph_works_core.workspace.config import WorkspaceConfig, load_workspace_config
from graph_works_core.workspace.errors import WorkspaceConfigError, WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout

from graph_works_cli import exit_codes
from graph_works_cli.work_cli import rendering
from graph_works_cli.workspace_resolution import resolve_workspace

decision_app = typer.Typer(name="decision", help="Epic decisions ledger.", no_args_is_help=True)


def _today() -> date:
    return datetime.now(UTC).date()


def _config(layout: WorkspaceLayout) -> WorkspaceConfig:
    try:
        return load_workspace_config(layout)
    except WorkspaceConfigError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), reason="io", cause=exc)


def _unknown_target(exc: ValueError) -> typer.Exit:
    """Unknown decision targets land on `AMBIGUOUS`."""
    rendering.fail(str(exc), reason="unresolved", code=exit_codes.AMBIGUOUS, cause=exc)


def _emit(payload: dict[str, object], *, verb: str, json_output: bool) -> None:
    warnings = payload["warnings"]
    assert isinstance(warnings, list)
    for warning in warnings:
        rendering.warn(str(warning))
    if payload["refusal"] is not None:
        rendering.fail(f"refused ({payload['refusal']}); nothing was applied", reason="refused", payload=payload)
    if payload["applied"] and (payload["rolled_back"] or payload["failures"]):
        rendering.fail("decision apply was incomplete", reason="incomplete-apply", payload=payload)
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_decision_write(payload, verb)


@decision_app.command()
def add(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    question: str = typer.Option(..., "--question", help="The question this decision settles."),
    status: str = typer.Option("open", "--status", help="answered|assumed|open|superseded."),
    answer: str = typer.Option("", "--answer", help="Renders as an **Answer:** block."),
    rationale: str = typer.Option("", "--rationale", help="Renders as a **Rationale:** block."),
    if_wrong: str = typer.Option("", "--if-wrong", help="Blast radius; renders as an **If wrong:** block."),
    affects: str = typer.Option("", "--affects", help="Comma-separated child slugs this decision affects."),
    hold: str = typer.Option("", "--hold", help="park|skip: file this open decision as a typed hold on PATH."),
    phase: str = typer.Option(
        "", "--phase", help="PATH's current phase (`entry` when it has none); required with --hold."
    ),
    checkpoint: str = typer.Option("", "--checkpoint", help="Draft checkpoint file a park copies under references/."),
    decided_by: str = typer.Option("user", "--decided-by", help="Actor recorded in `decided`."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan the append without writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option(""),
) -> None:
    """Append a decision (or a typed hold) to the owner's ledger."""
    layout = resolve_workspace(workspace)
    try:
        result = work.run_decision_add(
            layout,
            path,
            question=question,
            status=status,
            answer=answer or None,
            rationale=rationale or None,
            if_wrong=if_wrong or None,
            affects=rendering.split_csv(affects),
            hold=hold or None,
            phase=phase or None,
            checkpoint=Path(checkpoint) if checkpoint else None,
            on=_today(),
            decided_by=decided_by,
            dry_run=dry_run,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        _unknown_target(exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    _emit(rendering.decision_payload(result), verb="appended", json_output=json_output)


@decision_app.command(name="list")
def list_cmd(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    status: str = typer.Option("", "--status", help="Filter: answered|assumed|open|superseded."),
    affects: str = typer.Option("", "--affects", help="Filter: entries whose affects contain this work path."),
    cites: str = typer.Option("", "--cites", help="Filter: entries referencing this id, e.g. D-014."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option(""),
) -> None:
    """List the owning epic's decisions. A missing ledger reads as empty.

    `counts` rolls up the **whole** ledger, not the filtered slice -- the
    rollup is what makes a filtered view legible.
    """
    layout = resolve_workspace(workspace)
    try:
        result = work.run_decision_list(
            layout, path, status=status or None, affects=affects or None, cites=cites or None
        )
    except ValueError as exc:
        _unknown_target(exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = rendering.decision_payload(result)
    for warning in payload["warnings"]:
        rendering.warn(warning)
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_decision_list(payload)


@decision_app.command()
def answer(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    decision_id: str = typer.Argument(..., metavar="DECISION_ID", help="e.g. D-014."),
    answer_text: str = typer.Option(..., "--answer", help="Renders as an **Answer:** block."),
    rationale: str = typer.Option("", "--rationale", help="Renders as a **Rationale:** block."),
    decided_by: str = typer.Option("user", "--decided-by", help="Actor recorded in `decided`."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan the answer without writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option(""),
) -> None:
    """Flip an open or assumed decision to answered."""
    layout = resolve_workspace(workspace)
    try:
        result = work.run_decision_answer(
            layout,
            path,
            decision_id,
            answer=answer_text,
            rationale=rationale or None,
            on=_today(),
            decided_by=decided_by,
            dry_run=dry_run,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        _unknown_target(exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    _emit(rendering.decision_payload(result), verb="answered", json_output=json_output)


@decision_app.command()
def supersede(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    decision_id: str = typer.Argument(..., metavar="DECISION_ID", help="The id being retired, e.g. D-014."),
    question: str = typer.Option(..., "--question", help="The replacement entry's question."),
    answer_text: str = typer.Option(..., "--answer", help="Renders as an **Answer:** block."),
    rationale: str = typer.Option("", "--rationale", help="Renders as a **Rationale:** block."),
    affects: str = typer.Option("", "--affects", help="Comma-separated child slugs; omit to inherit."),
    decided_by: str = typer.Option("user", "--decided-by", help="Actor recorded in `decided`."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan the supersession without writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option(""),
) -> None:
    """Retire one decision and append its canonical replacement, under one lock."""
    layout = resolve_workspace(workspace)
    parsed_affects = rendering.split_csv(affects)
    try:
        result = work.run_decision_supersede(
            layout,
            path,
            decision_id,
            question=question,
            answer=answer_text,
            rationale=rationale or None,
            affects=parsed_affects or None,
            on=_today(),
            decided_by=decided_by,
            dry_run=dry_run,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        _unknown_target(exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    _emit(rendering.decision_payload(result), verb="appended", json_output=json_output)


@decision_app.command()
def overturn(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    decision_id: str = typer.Argument(..., metavar="DECISION_ID", help="The id being overturned, e.g. D-014."),
    answer_text: str = typer.Option(..., "--answer", help="The new decision; renders as an **Answer:** block."),
    follow_up_title: str = typer.Option(..., "--follow-up-title", help="Title of the follow-up work item."),
    follow_up_affects: str = typer.Option("", "--follow-up-affects", help="Comma-separated paths or packages."),
    follow_up_kind: str = typer.Option("TechDebt", "--follow-up-kind", help="Kind for the follow-up item."),
    rationale: str = typer.Option("", "--rationale", help="Renders as a **Rationale:** block."),
    decided_by: str = typer.Option("user", "--decided-by", help="Actor recorded in `decided`."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preflight both halves without writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option(""),
) -> None:
    """Supersede a decision and file a follow-up against what already landed.

    One logical operation. Both halves are preflighted before either writes,
    so an expected refusal on the follow-up leaves the ledger untouched too.
    """
    layout = resolve_workspace(workspace)
    config = _config(layout)
    try:
        result = work.run_decision_overturn(
            layout,
            config,
            path,
            decision_id,
            answer=answer_text,
            rationale=rationale or None,
            follow_up_title=follow_up_title,
            follow_up_type=follow_up_kind,
            follow_up_affects=rendering.split_csv(follow_up_affects),
            on=_today(),
            decided_by=decided_by,
            dry_run=dry_run,
        )
    except work.OverturnApplyError as exc:
        rendering.fail(
            f"overturn applied the ledger write but failed to file the follow-up: {exc}. "
            "The ledger and the work lane now disagree; reconcile by hand.",
            reason="incomplete-apply",
            cause=exc,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        _unknown_target(exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = rendering.overturn_payload(result)
    for warning in payload["warnings"]:
        rendering.warn(warning)
    if payload["refusal"] is not None:
        rendering.fail(
            f"refused ({payload['refusal']}); neither the ledger nor the work lane was written",
            reason="refused",
            payload=payload,
        )
    if payload["applied"] and (payload["rolled_back"] or payload["failures"]):
        rendering.fail("overturn apply rolled back", reason="incomplete-apply", payload=payload)
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_decision_write(payload, "appended")

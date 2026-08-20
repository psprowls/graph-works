"""`gw work decision` — the epic-owned decisions ledger, nested under `gw work`.

Every verb accepts **any** slug inside an epic's subtree: core walks up to the
owning epic and reports the redirect as `resolved_from`, so a fan-out subagent
can pass its own slug without knowing its epic.

`overturn` is one logical operation, not two commands: it retires the old
decision, records its replacement, and files the follow-up work item
describing remediation for what already landed. Core preflights both halves
before either write, so an expected refusal leaves both untouched.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import typer
from code_wiki_okf.config import Config, ConfigError, load_config
from graph_works_core.work import commands as work
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout

from graph_works_cli import exit_codes
from graph_works_cli.work_cli import rendering
from graph_works_cli.workspace_resolution import resolve_workspace

decision_app = typer.Typer(name="decision", help="Epic decisions ledger.", no_args_is_help=True)


def _today() -> date:
    return datetime.now(UTC).date()


def _config(layout: WorkspaceLayout) -> Config:
    try:
        return load_config(layout.bundle_dir)
    except ConfigError as exc:
        rendering.fail(str(exc), code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), cause=exc)


def _unknown_target(exc: ValueError) -> typer.Exit:
    """`run_decision_*` raises `ValueError` for both an unknown slug and a slug
    with no epic ancestor. Both are unresolved targets, so both land on
    `AMBIGUOUS` -- the message distinguishes them, the exit code does not."""
    rendering.fail(str(exc), code=exit_codes.AMBIGUOUS, cause=exc)


def _emit(payload: dict[str, object], *, verb: str, json_output: bool) -> None:
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_decision_write(payload, verb)
    if payload["refusal"] is not None:
        rendering.fail(f"refused ({payload['refusal']})")


@decision_app.command()
def add(
    slug: str = typer.Argument(..., help="Any slug in the epic's subtree; resolves to the owning epic."),
    question: str = typer.Option(..., "--question", help="The question this decision settles."),
    status: str = typer.Option("open", "--status", help="answered|assumed|open|superseded."),
    answer: str = typer.Option("", "--answer", help="Renders as an **Answer:** block."),
    rationale: str = typer.Option("", "--rationale", help="Renders as a **Rationale:** block."),
    if_wrong: str = typer.Option("", "--if-wrong", help="Blast radius; renders as an **If wrong:** block."),
    affects: str = typer.Option("", "--affects", help="Comma-separated child slugs this decision affects."),
    decided_by: str = typer.Option("user", "--decided-by", help="Actor recorded in `decided`."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan the append without writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Append a decision to the owning epic's ledger."""
    layout = resolve_workspace(workspace)
    try:
        result = work.run_decision_add(
            layout,
            slug,
            question=question,
            status=status,
            answer=answer or None,
            rationale=rationale or None,
            if_wrong=if_wrong or None,
            affects=rendering.split_csv(affects),
            on=_today(),
            decided_by=decided_by,
            dry_run=dry_run,
        )
    except ValueError as exc:
        _unknown_target(exc)
    except OSError as exc:
        rendering.fail(str(exc), cause=exc)
    _emit(rendering.decision_payload(result), verb="appended", json_output=json_output)


@decision_app.command(name="list")
def list_cmd(
    slug: str = typer.Argument(..., help="Any slug in the epic's subtree."),
    status: str = typer.Option("", "--status", help="Filter: answered|assumed|open|superseded."),
    affects: str = typer.Option("", "--affects", help="Filter: entries whose affects contain this slug."),
    cites: str = typer.Option("", "--cites", help="Filter: entries referencing this id, e.g. D-014."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List the owning epic's decisions. A missing ledger reads as empty.

    `counts` rolls up the **whole** ledger, not the filtered slice -- the
    rollup is what makes a filtered view legible.
    """
    layout = resolve_workspace(workspace)
    try:
        result = work.run_decision_list(
            layout, slug, status=status or None, affects=affects or None, cites=cites or None
        )
    except ValueError as exc:
        _unknown_target(exc)
    except OSError as exc:
        rendering.fail(str(exc), cause=exc)

    payload = rendering.decision_payload(result)
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_decision_list(payload)


@decision_app.command()
def answer(
    slug: str = typer.Argument(..., help="Any slug in the epic's subtree."),
    decision_id: str = typer.Argument(..., metavar="DECISION_ID", help="e.g. D-014."),
    answer_text: str = typer.Option(..., "--answer", help="Renders as an **Answer:** block."),
    rationale: str = typer.Option("", "--rationale", help="Renders as a **Rationale:** block."),
    decided_by: str = typer.Option("user", "--decided-by", help="Actor recorded in `decided`."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan the answer without writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Flip an open or assumed decision to answered."""
    layout = resolve_workspace(workspace)
    try:
        result = work.run_decision_answer(
            layout,
            slug,
            decision_id,
            answer=answer_text,
            rationale=rationale or None,
            on=_today(),
            decided_by=decided_by,
            dry_run=dry_run,
        )
    except ValueError as exc:
        _unknown_target(exc)
    except OSError as exc:
        rendering.fail(str(exc), cause=exc)
    _emit(rendering.decision_payload(result), verb="answered", json_output=json_output)


@decision_app.command()
def supersede(
    slug: str = typer.Argument(..., help="Any slug in the epic's subtree."),
    decision_id: str = typer.Argument(..., metavar="DECISION_ID", help="The id being retired, e.g. D-014."),
    question: str = typer.Option(..., "--question", help="The replacement entry's question."),
    answer_text: str = typer.Option(..., "--answer", help="Renders as an **Answer:** block."),
    rationale: str = typer.Option("", "--rationale", help="Renders as a **Rationale:** block."),
    affects: str = typer.Option("", "--affects", help="Comma-separated child slugs; omit to inherit."),
    decided_by: str = typer.Option("user", "--decided-by", help="Actor recorded in `decided`."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan the supersession without writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Retire one decision and append its canonical replacement, under one lock."""
    layout = resolve_workspace(workspace)
    parsed_affects = rendering.split_csv(affects)
    try:
        result = work.run_decision_supersede(
            layout,
            slug,
            decision_id,
            question=question,
            answer=answer_text,
            rationale=rationale or None,
            affects=parsed_affects or None,
            on=_today(),
            decided_by=decided_by,
            dry_run=dry_run,
        )
    except ValueError as exc:
        _unknown_target(exc)
    except OSError as exc:
        rendering.fail(str(exc), cause=exc)
    _emit(rendering.decision_payload(result), verb="appended", json_output=json_output)


@decision_app.command()
def overturn(
    slug: str = typer.Argument(..., help="Any slug in the epic's subtree."),
    decision_id: str = typer.Argument(..., metavar="DECISION_ID", help="The id being overturned, e.g. D-014."),
    answer_text: str = typer.Option(..., "--answer", help="The new decision; renders as an **Answer:** block."),
    follow_up_title: str = typer.Option(..., "--follow-up-title", help="Title of the follow-up work item."),
    follow_up_affects: str = typer.Option("", "--follow-up-affects", help="Comma-separated paths or packages."),
    follow_up_kind: str = typer.Option("TechDebt", "--follow-up-kind", help="Kind for the follow-up item."),
    rationale: str = typer.Option("", "--rationale", help="Renders as a **Rationale:** block."),
    decided_by: str = typer.Option("user", "--decided-by", help="Actor recorded in `decided`."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preflight both halves without writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json"),
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
            slug,
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
            cause=exc,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        _unknown_target(exc)
    except OSError as exc:
        rendering.fail(str(exc), cause=exc)

    payload = rendering.overturn_payload(result)
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_decision_write(payload, "appended")
    if payload["refusal"] is not None:
        rendering.fail(f"refused ({payload['refusal']}); neither the ledger nor the work lane was written")

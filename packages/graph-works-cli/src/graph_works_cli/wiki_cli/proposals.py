"""Open proposal listing for ``gw wiki``."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import typer
from graph_works_core.proposals import run_proposal_decide, run_proposal_file, run_proposals_read
from graph_works_wire.wiki import proposal_decide_payload, proposal_file_payload, proposals_payload
from okf_ext.proposals import ApplyResult, Decision, Write

from graph_works_cli.errors import fail
from graph_works_cli.json_output import encode
from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.workspace_resolution import resolve_workspace


def proposals(
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """List open proposals."""
    layout = resolve_workspace(workspace)
    try:
        found = run_proposals_read(layout)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    payload = proposals_payload(found)
    if json_output:
        typer.echo(encode(payload))
        return
    if not payload:
        typer.echo("no open proposals")
        return
    for proposal in payload:
        typer.echo(f"{proposal['target']}: {proposal['page_status']} (malformed: {proposal['malformed'] or 'no'})")


def _report(
    payload: dict[str, object],
    writes: Sequence[Write],
    result: ApplyResult | None,
    *,
    dry_run: bool,
    json_output: bool,
) -> None:
    """One success rendering for all three verbs."""
    if json_output:
        typer.echo(encode(payload))
        return
    members = [write.member for write in writes] if dry_run else ([] if result is None else list(result.written))
    if not members:
        typer.echo("nothing to do")
        return
    for member in members:
        typer.echo(member)


def _decide(target: str, decision: Decision, workspace: str, *, dry_run: bool, json_output: bool, command: str) -> None:
    """Record one human decision against a proposal target."""
    layout = resolve_workspace(workspace, json_mode=json_output, command=command)
    try:
        run = run_proposal_decide(layout, target, decision, by="human", at=datetime.now(UTC), dry_run=dry_run)
    except (OSError, ValueError) as exc:
        fail(str(exc), reason="io", json_mode=json_output, command=command, cause=exc)
    payload = proposal_decide_payload(run)
    if run.proposal is None:
        fail(
            f"no proposal targets {target!r} in {layout.bundle_dir}",
            reason="refused",
            json_mode=json_output,
            command=command,
            payload=payload,
        )
    if run.refusals:
        fail(
            "proposal decision plan was refused",
            reason="refused",
            json_mode=json_output,
            command=command,
            payload=payload,
        )
    if run.result is not None and not run.result.ok:
        fail(
            "proposal decision was incomplete",
            reason="incomplete-apply",
            json_mode=json_output,
            command=command,
            payload=payload,
        )
    _report(
        payload, run.plan.writes if run.plan is not None else (), run.result, dry_run=dry_run, json_output=json_output
    )


def file_proposal(
    lane: str = typer.Option(..., "--lane"),
    title: str = typer.Option(..., "--title"),
    description: str = typer.Option("", "--description"),
    identifier: str = typer.Option(..., "--id"),
    resource: str = typer.Option(..., "--resource"),
    rationale: str = typer.Option("", "--rationale"),
    evidence: list[str] | None = typer.Option(None, "--evidence"),  # noqa: B008 -- Typer CLI declaration
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan without writing proposal files."),
    json_output: bool = typer.Option(False, "--json", help="Print the proposal projection instead of text."),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """File or merge one source's proposal for a lane target."""
    command = "wiki proposal file"
    layout = resolve_workspace(workspace, json_mode=json_output, command=command)
    source: dict[str, Any] = {"id": identifier, "resource": resource}
    if rationale:
        source["rationale"] = rationale
    if evidence:
        source["evidence"] = list(evidence)

    try:
        run = run_proposal_file(
            layout,
            lane=lane,
            title=title,
            description=description,
            source=source,
            by="agent:graph-works-cli",
            at=datetime.now(UTC),
            dry_run=dry_run,
        )
    except KeyError as exc:
        fail(str(exc), reason="usage", json_mode=json_output, command=command, cause=exc)
    except (OSError, ValueError) as exc:
        fail(str(exc), reason="io", json_mode=json_output, command=command, cause=exc)
    if run.refusals:
        fail(
            "proposal filing plan was refused",
            reason="refused",
            json_mode=json_output,
            command=command,
            payload=proposal_file_payload(run),
        )
    if run.result is not None and not run.result.ok:
        fail(
            "proposal filing was incomplete",
            reason="incomplete-apply",
            json_mode=json_output,
            command=command,
            payload=proposal_file_payload(run),
        )
    _report(proposal_file_payload(run), run.plan.writes, run.result, dry_run=dry_run, json_output=json_output)


def approve(
    target: str = typer.Argument(...),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan without writing proposal files."),
    json_output: bool = typer.Option(False, "--json", help="Print the proposal projection instead of text."),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Approve the proposal identified by TARGET."""
    _decide(target, "approved", workspace, dry_run=dry_run, json_output=json_output, command="wiki proposal approve")


def reject(
    target: str = typer.Argument(...),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan without writing proposal files."),
    json_output: bool = typer.Option(False, "--json", help="Print the proposal projection instead of text."),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Reject the proposal identified by TARGET."""
    _decide(target, "rejected", workspace, dry_run=dry_run, json_output=json_output, command="wiki proposal reject")


proposal_app = typer.Typer(name="proposal", help="File or decide one proposal.", no_args_is_help=True)
proposal_app.command(name="file")(file_proposal)
proposal_app.command(name="approve")(approve)
proposal_app.command(name="reject")(reject)

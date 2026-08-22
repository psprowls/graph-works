"""Open proposal listing for ``gw wiki``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

import typer
from code_wiki_okf.config import ConfigError, load_config
from doc_wiki_okf.proposals import lane_set, plan_file
from okf_ext.proposals import Decision, Proposal, apply, list_proposals, plan_decide
from okf_ext.schemas import load_schemas
from okf_io import Bundle, load_bundle

from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.wiki_cli.rendering import proposal_payload
from graph_works_cli.workspace_resolution import resolve_workspace


def _normalized_target(target: str) -> str:
    """Turn CLI text into a usable bundle-relative proposal identity, or ``""``."""
    cleaned = target.strip().replace("\\", "/").lstrip("/")
    if not cleaned:
        return ""
    parts: list[str] = []
    for part in PurePosixPath(cleaned).parts:
        if part == ".":  # pragma: no cover -- PurePosixPath.parts never yields "."; kept as a
            continue  # guard in case the normalization above stops routing through PurePosixPath
        if part == "..":
            if not parts:
                return ""
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _find_by_target(bundle: Bundle, target: str) -> Proposal | None:
    """Find a proposal exclusively through its normalized target identity."""
    wanted = _normalized_target(target)
    if not wanted:
        return None
    for proposal in list_proposals(bundle):
        if proposal.target == wanted:
            return proposal
    return None


def proposals(
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """List open proposals."""
    layout = resolve_workspace(workspace)
    try:
        found = list_proposals(load_bundle(layout.bundle_dir), page_status="proposed")
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    payload = [proposal_payload(proposal) for proposal in found]
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    if not payload:
        typer.echo("no open proposals")
        return
    for proposal in payload:
        typer.echo(f"{proposal['target']}: {proposal['page_status']} (malformed: {proposal['malformed'] or 'no'})")


def _decide(target: str, decision: Decision, workspace: str) -> None:
    """Record one human decision against a proposal target."""
    layout = resolve_workspace(workspace)
    try:
        bundle = load_bundle(layout.bundle_dir)
        proposal = _find_by_target(bundle, target)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)
    if proposal is None:
        exit_error(f"no proposal targets {target!r} in {bundle.root}")

    now = datetime.now(UTC)
    try:
        plan = plan_decide(bundle, proposal, decision, by="human", at=now)
    except ValueError as exc:
        exit_error(str(exc), cause=exc)
    if not plan.ok:
        exit_error("proposal decision plan was refused")
    if plan.is_empty:
        typer.echo("nothing to do")
        return

    try:
        result = apply(bundle, plan)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)
    if not result.ok:
        exit_error("proposal decision was incomplete")
    for member in result.written:
        typer.echo(member)


def file_proposal(
    lane: str = typer.Option(..., "--lane"),
    title: str = typer.Option(..., "--title"),
    description: str = typer.Option("", "--description"),
    identifier: str = typer.Option(..., "--id"),
    resource: str = typer.Option(..., "--resource"),
    rationale: str = typer.Option("", "--rationale"),
    evidence: list[str] | None = typer.Option(None, "--evidence"),  # noqa: B008 -- Typer CLI declaration
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """File or merge one source's proposal for a lane target."""
    layout = resolve_workspace(workspace)
    try:
        bundle = load_bundle(layout.bundle_dir)
        config = load_config(
            layout.bundle_dir,
            config_path=layout.manifest_path,
            graph_dir=layout.cache_dir,
            declarations_dir=layout.config_dir,
        )
        lanes = lane_set(load_schemas(config.declarations_dir / "_schema"))
    except (ConfigError, OSError, ValueError, KeyError) as exc:
        exit_error(str(exc), cause=exc)

    source: dict[str, Any] = {"id": identifier, "resource": resource}
    if rationale:
        source["rationale"] = rationale
    if evidence:
        source["evidence"] = list(evidence)

    try:
        plan = plan_file(
            bundle,
            lanes,
            lane=lane,
            title=title,
            description=description,
            source=source,
            by="agent:graph-works-cli",
            at=datetime.now(UTC),
        )
    except (KeyError, ValueError) as exc:
        exit_error(str(exc), cause=exc)
    if not plan.ok:
        exit_error("proposal filing plan was refused")
    if plan.is_empty:
        typer.echo("nothing to do")
        return

    try:
        result = apply(bundle, plan)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)
    if not result.ok:
        exit_error("proposal filing was incomplete")
    for member in result.written:
        typer.echo(member)


def approve(
    target: str = typer.Argument(...),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Approve the proposal identified by TARGET."""
    _decide(target, "approved", workspace)


def reject(
    target: str = typer.Argument(...),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Reject the proposal identified by TARGET."""
    _decide(target, "rejected", workspace)


proposal_app = typer.Typer(name="proposal", help="File or decide one proposal.", no_args_is_help=True)
proposal_app.command(name="file")(file_proposal)
proposal_app.command(name="approve")(approve)
proposal_app.command(name="reject")(reject)

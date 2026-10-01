"""`gw repo` — reference and managed repositories in the `repositories/` lane (D-001).

Routes to core and passes the clock as `now`. Refused add and advance results
emit the wire refusal envelope under `--json`. Restore prints every outcome
and exits nonzero when any repository was refused.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from graph_works_core.repositories.adopt import run_repo_adopt
from graph_works_core.repositories.commands import Rescan, run_repo_add, run_repo_advance, run_repo_restore
from graph_works_core.scan.commands import run_scan
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.errors import ScanError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_wire.repo import repo_add_payload, repo_adopt_payload, repo_advance_payload, repo_restore_payload

from graph_works_cli import exit_codes
from graph_works_cli.errors import fail
from graph_works_cli.json_output import encode
from graph_works_cli.repo_cli import rendering
from graph_works_cli.workspace_resolution import resolve_workspace

repo_app = typer.Typer(
    name="repo",
    help="Repository clones pinned in the repositories/ lane, with optional managed working checkouts.",
    no_args_is_help=True,
)


def _structural_rescan(layout: WorkspaceLayout) -> Rescan:
    """Run a structural scan after advancing a managed repository."""

    def rescan() -> Sequence[str]:
        now = datetime.now(UTC)
        try:
            result = asyncio.run(
                run_scan(layout, load_workspace_config(layout), today=now.date(), at=now, narrate=False, dry_run=False)
            )
        except (OSError, ScanError, ValueError) as exc:
            return (str(exc),)
        return tuple(result.errors)

    return rescan


@repo_app.command("add")
def add(
    url: str = typer.Argument(..., help="Repository URL to clone."),
    name: str = typer.Option("", "--name", help="Lane name. Defaults to the URL's last segment without .git."),
    track: str = typer.Option("", "--track", help="Branch to follow. Defaults to the remote's default branch."),
    ref: str = typer.Option("", "--ref", help="For a reference repository: branch, tag or SHA to pin."),
    managed: bool = typer.Option(
        False, "--managed", help="Develop this repository in a working checkout and declare it in workspace.yaml."
    ),
    checkout: str = typer.Option(
        "", "--checkout", help="With --managed: working checkout path. Defaults to .gw/worktrees/<name>/<track>."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Resolve the commit and report planned writes; create no clone or checkout."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root."),
) -> None:
    """Clone a repository at a pin, record its lane page, and optionally create a managed checkout."""
    if managed and ref:
        raise typer.BadParameter(
            "--ref does not apply with --managed: a managed repository is pinned at the tip of --track",
            param_hint="--ref",
        )
    if checkout and not managed:
        raise typer.BadParameter("--checkout applies only with --managed", param_hint="--checkout")
    layout = resolve_workspace(workspace, json_mode=json_output, command="repo add")
    result = run_repo_add(
        layout,
        url,
        name=name or None,
        track=track or None,
        ref=ref or None,
        managed=managed,
        checkout=Path(checkout).expanduser().resolve() if checkout else None,
        now=datetime.now(UTC),
        dry_run=dry_run,
    )
    payload = repo_add_payload(result)
    if result.refusal is not None:
        fail(
            f"{result.refusal.code}: {result.refusal.detail}",
            reason="refused",
            json_mode=json_output,
            command="repo add",
            payload=payload,
        )
    typer.echo(encode(payload) if json_output else rendering.add_text(result))


@repo_app.command("restore")
def restore(
    names: Annotated[
        list[str] | None, typer.Argument(help="Repositories to restore. Omit for every page in the lane.")
    ] = None,
    dry_run: bool = typer.Option(False, "--dry-run", help="Report clone and checkout outcomes; change nothing."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root."),
) -> None:
    """Restore pinned clones and any managed working checkouts. Writes no page."""
    layout = resolve_workspace(workspace, json_mode=json_output, command="repo restore")
    result = run_repo_restore(layout, names or (), dry_run=dry_run)
    payload = repo_restore_payload(result)
    if result.refusal is not None:
        fail(
            f"{result.refusal.code}: {result.refusal.detail}",
            reason="refused",
            json_mode=json_output,
            command="repo restore",
            payload=payload,
        )
    typer.echo(encode(payload) if json_output else rendering.restore_text(result))
    if not result.ok:
        raise typer.Exit(code=exit_codes.GENERIC)


@repo_app.command("advance")
def advance(
    name: str = typer.Argument(..., help="The repository to advance."),
    to: str = typer.Option("", "--to", help="For a reference repository: branch, tag or SHA to advance to."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report the target commit, plus range and flagged pages for a reference; move nothing."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root."),
) -> None:
    """Move a repository's pin forward: a reference repository records a snapshot, a changelog entry and
    proposals; a managed repository detaches at its track's tip and rescans.
    """
    layout = resolve_workspace(workspace, json_mode=json_output, command="repo advance")
    result = run_repo_advance(
        layout, name, to=to or None, now=datetime.now(UTC), dry_run=dry_run, rescan=_structural_rescan(layout)
    )
    payload = repo_advance_payload(result)
    if result.refusal is not None:
        fail(
            f"{result.refusal.code}: {result.refusal.detail}",
            reason="refused",
            json_mode=json_output,
            command="repo advance",
            payload=payload,
        )
    typer.echo(encode(payload) if json_output else rendering.advance_text(result))


@repo_app.command("adopt")
def adopt(
    name: str = typer.Argument(..., help="The repositories.<name> entry whose checkout moves into the lane."),
    track: str | None = typer.Option(
        None, "--track", help="Branch for the working checkout; defaults to the checked-out branch."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report planned adoption; change nothing."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root."),
) -> None:
    """Move a declared checkout into okf/repositories/<name>/references/git as a managed repository."""
    layout = resolve_workspace(workspace, json_mode=json_output, command="repo adopt")
    result = run_repo_adopt(layout, name, track=track, now=datetime.now(UTC), dry_run=dry_run)
    payload = repo_adopt_payload(result)
    if result.refusal is not None:
        fail(
            f"{result.refusal.code}: {result.refusal.detail}",
            reason="refused",
            json_mode=json_output,
            command="repo adopt",
            payload=payload,
        )
    typer.echo(encode(payload) if json_output else rendering.adopt_text(result))

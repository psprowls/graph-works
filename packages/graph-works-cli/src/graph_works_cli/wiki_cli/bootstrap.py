"""The root-level workspace bootstrap command."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import typer
from graph_works_core import InitError, apply_init, plan_init
from graph_works_core.workspace.discovery import resolve_root

from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.wiki_cli.rendering import bootstrap_payload, bootstrap_plan_payload


def bootstrap(
    topic: str = typer.Option(..., "--topic"),
    workspace: str = typer.Option("", "--workspace"),
    repo_root: str = typer.Option("", "--repo-root"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Create a graph-works workspace if it has not been initialized yet."""
    # `--repo-root` reaches `plan_init` unvalidated: the path need not exist and
    # need not carry a `.git`. That is the flag's point — it names a repo the
    # walk-up cannot find — so a typo is accepted and written into
    # `workspace.yaml`'s `repositories:` block just as a real path would be.
    # `--dry-run` previews it.
    now = datetime.now(UTC)
    root = resolve_root(workspace=workspace or None, cwd=Path.cwd(), environ=os.environ)
    try:
        plan = plan_init(root, today=now.date(), topic=topic, repo_root=repo_root or None)
    except (InitError, OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)
    if not plan.ok:
        exit_error("workspace initialization plan was refused")

    if dry_run:
        if json_output:
            typer.echo(json.dumps(bootstrap_plan_payload(plan), indent=2))
        else:
            typer.echo(plan.diff() or "nothing to do")
        return

    result = apply_init(plan)
    if json_output:
        typer.echo(json.dumps(bootstrap_payload(result), indent=2))
    else:
        typer.echo(result.diff() or "nothing to do")
    if not result.ok:
        exit_error("workspace initialization was incomplete")

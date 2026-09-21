"""`gw wiki tags` — inventory, draft, apply, gate.

ADR 2026-08-13-command-modules: this module routes, parses, formats, and traces. Every decision --
what the retention rule is, what a disposition file may say, which phase runs
first -- belongs to `graph_works_core.tag_policy` and is not restated here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer
from graph_works_core.tag_policy.commands import (
    Phase,
    apply_phase,
    draft_disposition,
    plan_phase,
    undeclared,
)
from graph_works_core.tag_policy.disposition import load, render
from graph_works_wire.wiki import tag_inventory_payload, tags_undeclared_payload
from okf_ext.tags import VOCABULARY_FILENAME
from okf_ext.tags import inventory as tag_inventory
from okf_io import load_bundle

from graph_works_cli import exit_codes
from graph_works_cli.json_output import encode
from graph_works_cli.wiki_cli.errors import exit_error
from graph_works_cli.workspace_resolution import resolve_workspace

tags_app = typer.Typer(name="tags", help="Tag inventory, retention policy, and gate.", no_args_is_help=True)


@tags_app.command(name="inventory")
def inventory_command(
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Report every tag the vault carries, with its page count."""
    layout = resolve_workspace(workspace)
    try:
        result = tag_inventory(load_bundle(layout.bundle_dir))
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    payload = tag_inventory_payload(result)
    if json_output:
        typer.echo(encode(payload))
        return

    typer.echo(f"{payload['total_tags']} distinct tags across {payload['tagged_pages']} tagged pages")
    # Commonest first; ties lexicographic, so two runs over one bundle agree.
    for tag, count in sorted(result.counts.items(), key=lambda item: (-item[1], item[0])):
        typer.echo(f"{count:>5}  {tag}")


@tags_app.command(name="draft")
def draft_command(
    out: str = typer.Argument(...),
    as_of: str = typer.Option(..., "--as-of", help="Generation date, YYYY-MM-DD. Required: the CLI owns the clock."),
    floor: int = typer.Option(5, "--floor", min=0),
    ceiling: float = typer.Option(0.40, "--ceiling", min=0.0, max=1.0),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Write the reviewable disposition from the retention rule."""
    layout = resolve_workspace(workspace)
    out_path = Path(out)
    try:
        generated = date.fromisoformat(as_of)
    except ValueError as exc:
        exit_error(f"--as-of must be YYYY-MM-DD: {exc}", cause=exc)
    try:
        disposition = draft_disposition(
            load_bundle(layout.bundle_dir), generated=generated, floor=floor, ceiling=ceiling
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render(disposition), encoding="utf-8", newline="")
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    typer.echo(
        f"{out_path}: {len(disposition.keep)} keep, {len(disposition.strip)} strip, {disposition.total_tags} total"
    )


@tags_app.command(name="apply")
def apply_command(
    disposition: str = typer.Argument(..., metavar="DISPOSITION"),
    only: str = typer.Option("", "--only", help="Run one phase: merge or strip. Omitted, both run in that order."),
    dry_run: bool = typer.Option(False, "--dry-run"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Execute a reviewed disposition against the vault.

    **`--dry-run` with both phases planned prints pre-merge strip indices.**
    The loop below calls `plan_phase` for each phase in turn without applying
    either, so when both `merge` and `strip` run the strip preview's
    `path[index]` lines are computed against the *pre-merge* bundle -- the
    edits `apply_phase` would actually make are unaffected (a real, non-dry
    run always applies merge before planning strip against its result), only
    the dry-run preview's printed indices can be off from what a subsequent
    real run would show for `strip`.
    """
    layout = resolve_workspace(workspace)
    phases: tuple[Phase, ...] = ("merge", "strip")
    if only:
        if only not in phases:
            exit_error(f"--only must be `merge` or `strip`, not {only!r}", code=exit_codes.AMBIGUOUS)
        phases = (only,)

    try:
        bundle = load_bundle(layout.bundle_dir)
        loaded_disposition = load(Path(disposition))
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    incomplete = False
    for phase in phases:
        try:
            if dry_run:
                plan = plan_phase(bundle, loaded_disposition, phase)
                typer.echo(f"{phase}: {len(plan.edits)} edits across {len(plan.concept_ids)} pages")
                for edit in plan.edits:
                    typer.echo(f"  {edit.path}[{edit.index}] {edit.old} -> {edit.new or '(removed)'}")
                continue
            result = apply_phase(bundle, loaded_disposition, phase)
        except (OSError, ValueError) as exc:
            exit_error(str(exc), cause=exc)
        typer.echo(f"{phase}: wrote {len(result.written)} pages")
        for failure in result.failed:
            typer.echo(f"  {failure.path}: {failure.kind}: {failure.error}", err=True)
        incomplete = incomplete or not result.ok

    if incomplete:
        exit_error("tag disposition was applied incompletely", code=exit_codes.GENERIC)


@tags_app.command(name="gate")
def gate_command(
    json_output: bool = typer.Option(False, "--json"),
    workspace: str = typer.Option("", "--workspace"),
) -> None:
    """Exit non-zero if the vault carries a tag the vocabulary does not know."""
    layout = resolve_workspace(workspace)
    try:
        missing = undeclared(load_bundle(layout.bundle_dir), layout.config_dir / VOCABULARY_FILENAME)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    if json_output:
        typer.echo(encode(tags_undeclared_payload(missing)))
    elif missing:
        for tag in missing:
            typer.echo(tag)
    else:
        typer.echo("every tag is declared")

    if missing:
        exit_error(f"{len(missing)} undeclared tag(s)", code=exit_codes.GENERIC)

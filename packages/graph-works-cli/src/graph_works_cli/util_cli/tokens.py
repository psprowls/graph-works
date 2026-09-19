"""`gw util tokens` — count every page offline and stamp `tokens:` frontmatter.

Core returns the three buckets as sorted data with skip *reasons* rather than printing
`[warn] skipping …` itself, so this layer decides the phrasing: the human view caps each
bucket at twenty paths so a large vault stays readable, and `--json` carries every member.
`--dry-run` is opt-in, matching the legacy verb, so the flag is passed explicitly rather
than relying on core's `dry_run=True` default.
"""

from __future__ import annotations

import typer
from graph_works_core.util.commands import run_tokens_update
from graph_works_wire.util import tokens_payload

from graph_works_cli.errors import exit_error
from graph_works_cli.json_output import encode
from graph_works_cli.workspace_resolution import resolve_workspace

HUMAN_BUCKET_CAP = 20


def _render_bucket(label: str, members: list[str]) -> list[str]:
    """One `label: N` header plus at most `HUMAN_BUCKET_CAP` members, then the remainder."""
    lines = [f"{label}: {len(members)}"]
    lines.extend(f"  {member}" for member in members[:HUMAN_BUCKET_CAP])
    if len(members) > HUMAN_BUCKET_CAP:
        lines.append(f"  … and {len(members) - HUMAN_BUCKET_CAP} more")
    return lines


def tokens(
    dry_run: bool = typer.Option(False, "--dry-run", help="Count and report, but stamp nothing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace root; defaults to discovery."),
    json_output: bool = typer.Option(False, "--json", help="Emit every bucket in full as JSON."),
) -> None:
    """Stamp each page's token count into its frontmatter."""
    layout = resolve_workspace(workspace)
    try:
        update = run_tokens_update(layout, dry_run=dry_run)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    if json_output:
        typer.echo(encode(tokens_payload(update)))
        return

    lines: list[str] = []
    lines.extend(_render_bucket("updated", [f"{stamp.page} ({stamp.tokens})" for stamp in update.updated]))
    lines.extend(_render_bucket("unchanged", [f"{stamp.page} ({stamp.tokens})" for stamp in update.unchanged]))
    lines.extend(_render_bucket("skipped", [f"{page.page} ({page.reason})" for page in update.skipped]))
    typer.echo("\n".join(lines))

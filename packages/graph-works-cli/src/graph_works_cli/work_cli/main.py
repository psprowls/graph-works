"""`gw work` — the work-item pipeline verbs.

Interface band only (ADR-0013): parse arguments, resolve the workspace, call
**one** `graph-works-core` command, project the typed result, choose an exit
code. No business composition lives here, and nothing under
`graph_works_cli` imports `work_tracker_okf` -- `test_work_surface.py`
asserts it.

The clock is read here and nowhere below: every core writer takes `today=`
or `on=` and never looks it up itself.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import typer
from code_wiki_okf.config import Config, ConfigError, load_config
from graph_works_core.archive.commands import run_archive
from graph_works_core.orchestrate.commands import run_orchestrate, run_stage_advance
from graph_works_core.work import commands as work
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.pipeline import entry_for

from graph_works_cli import exit_codes
from graph_works_cli.provenance import warn_if_stale_routing
from graph_works_cli.work_cli import rendering
from graph_works_cli.work_cli.decision import decision_app
from graph_works_cli.work_cli.reconcile import reconcile_context
from graph_works_cli.workspace_resolution import resolve_workspace

work_app = typer.Typer(name="work", help="Work-item pipeline verbs.", no_args_is_help=True)
work_app.add_typer(decision_app, name="decision")
work_app.command(name="reconcile-context")(reconcile_context)


def _today() -> date:
    """One UTC date per invocation. Core never reads the clock (spec 6)."""
    return datetime.now(UTC).date()


def _config(layout: WorkspaceLayout) -> Config:
    """The workspace's own declarations, or a mapped configuration failure."""
    try:
        return load_config(layout.bundle_dir)
    except ConfigError as exc:
        rendering.fail(str(exc), code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), cause=exc)


_DEP_KEYS = ("slug", "blocks", "needs")


def _parse_dep_spec(raw: str) -> dict[str, str]:
    """One `--dep slug=X[,blocks=P][,needs=N]` value as a mapping.

    Key-value pairs rather than invented punctuation: `planning-epics` is the
    main machine author of these, so verbosity is cheap and self-documentation
    is not. Keys are case-sensitive by design -- `SLUG=a` is an unknown key,
    not a normalized `slug=a`. Syntax only; the edge **vocabulary** is core's
    to validate, through `parse_dependencies` below.
    """
    pairs: dict[str, str] = {}
    for part in (fragment.strip() for fragment in raw.split(",")):
        if not part:
            continue
        if "=" not in part:
            rendering.fail(f"--dep {raw!r}: expected key=value pairs, got {part!r}")
        key, _, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if key not in _DEP_KEYS:
            rendering.fail(f"--dep {raw!r}: unknown key {key!r}; expected slug|blocks|needs")
        if key in pairs:
            rendering.fail(f"--dep {raw!r}: duplicate key {key!r}")
        pairs[key] = value
    if not pairs.get("slug"):
        rendering.fail(f"--dep {raw!r}: 'slug=' is required and must not be empty")
    return pairs


def _dependency_edges(depends_on: str, dep_specs: list[str]) -> tuple[work.DependencyEdge, ...]:
    """`--depends-on` (terminal-gating CSV) unioned with `--dep` (phase-granular).

    Both arms go through core's `parse_dependencies`, which owns the closed
    `blocks`/`needs` vocabulary. The CLI parses syntax and reports issues; it
    does not decide what a valid edge is.
    """
    # Build raw list and origins list in exact same append order for positional error reporting
    raw: list[object] = [*rendering.split_csv(depends_on)]
    origins: list[str] = list(rendering.split_csv(depends_on))

    for spec in dep_specs:
        parsed_spec = _parse_dep_spec(spec)
        raw.append(parsed_spec)
        origins.append(spec)

    parsed = work.parse_dependencies(raw)
    if parsed.issues:
        detail = "; ".join(f"{issue.code}: {issue.detail} ({origins[issue.index]!r})" for issue in parsed.issues)
        rendering.fail(f"--dep/--depends-on: {detail}")
    return parsed.edges


@work_app.command()
def file(
    title: str = typer.Option(..., "--title", help="Work item title."),
    kind: str = typer.Option(..., "--kind", help="Epic | Feature | Bug | TechDebt | TestGap | Spike."),
    summary: str = typer.Option(..., "--summary", help="One-line summary for the index entry."),
    affects: str = typer.Option("", "--affects", help="Comma-separated repo paths or package names."),
    effort: str = typer.Option("", "--effort", help="xtra-small|small|medium|large|xtra-large."),
    slug_words: str = typer.Option("", "--slug-words", help="1-4 words for the slug. Defaults to the title."),
    parent: str = typer.Option("", "--parent", help="Parent slug; this item becomes its child."),
    depends_on: str = typer.Option("", "--depends-on", help="Comma-separated sibling slugs that must finish first."),
    dep: list[str] = typer.Option(  # noqa: B008 -- Typer declares CLI options in defaults
        [],
        "--dep",
        help="Repeatable phase-granular edge: slug=<slug>[,blocks=design|plan|execute|finish]"
        "[,needs=design|plan|execute|resolved].",
    ),
    blast_radius: str = typer.Option("", "--blast-radius", help="file|package|domain|system."),
    target: str = typer.Option("", "--target", help="YYYY-QN or YYYY-MM."),
    owner: str = typer.Option("", "--owner", help="Owner handle."),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the filing as JSON."),
) -> None:
    """File one work item, reconcile `work/index.md`, and log its arrival.

    `--parent` and `--depends-on`/`--dep` are mutually exclusive on one call:
    a child's dependency edges are expressed relative to its siblings, not to
    its epic. Core enforces that; this command only reports the refusal.
    """
    layout = resolve_workspace(workspace)
    config = _config(layout)
    edges = _dependency_edges(depends_on, dep)
    try:
        outcome = work.run_file(
            layout,
            config,
            type=kind,
            title=title,
            description=summary,
            on=_today(),
            words=slug_words or None,
            effort=effort or None,
            blast_radius=blast_radius or None,
            target=target or None,
            owner=owner or None,
            parent=parent or None,
            depends_on=edges,
            affects=rendering.split_csv(affects),
            tags=rendering.split_csv(tags),
            dry_run=dry_run,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), cause=exc)

    payload = rendering.file_payload(outcome)
    if outcome.plan.refusal is not None:
        for warning in payload["warnings"]:
            rendering.warn(warning)
        rendering.fail(f"{payload['slug']}: refused ({payload['refusal']}) — {payload['detail']}")

    if json_output:
        rendering.emit(payload)
        return
    for warning in payload["warnings"]:
        rendering.warn(warning)
    if dry_run:
        typer.echo(outcome.plan.filing.diff())
        return
    typer.echo(f"[ok] filed {payload['slug']}: {payload['page_path']}")
    for path in payload["indexes"]:
        typer.echo(f"  reconciled {path}")
    if payload["logged"]:
        typer.echo(f"  log.md: {payload['logged'].removeprefix('- ')}")


@work_app.command()
def status(
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the rollup as JSON."),
) -> None:
    """Count the active work items and name the one worth resuming."""
    layout = resolve_workspace(workspace)
    try:
        report = work.run_status(layout)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), cause=exc)

    payload = rendering.status_payload(report)
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_status(payload)


@work_app.command()
def lint(
    strict: bool = typer.Option(False, "--strict", help="Promote every warning to an error."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
) -> None:
    """Report work-lane conformance. Never writes.

    The fast, synchronous, LLM-free work-lane check -- a sibling of
    `gw wiki lint`, not a subset of it.
    """
    layout = resolve_workspace(workspace)
    config = _config(layout)
    try:
        report = work.run_lint(layout, config, repo_root=layout.repo_root, strict=strict, today=_today())
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), cause=exc)

    if json_output:
        rendering.emit(rendering.lint_payload(report))
    else:
        rendering.render_lint(report)
    if not report.ok:
        raise typer.Exit(code=exit_codes.GENERIC)


@work_app.command(name="next")
def next_stage(
    slug: str = typer.Argument(..., help="Work item slug (file stem under work/)."),
    descend: bool = typer.Option(
        False, "--descend", help="When the item waits on children, switch to the next actionable child."
    ),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the routing decision as JSON."),
) -> None:
    """Compute what to dispatch for SLUG, and what advancing would change.

    Its one permitted write is the design-spec pointer repair reported as
    `normalized` -- it is applied, not previewed, so the next read agrees with
    this one.
    """
    warn_if_stale_routing()
    layout = resolve_workspace(workspace)
    try:
        result = work.run_next(layout, slug, descend=descend, dry_run=False)
    except WorkspaceError as exc:
        rendering.fail(str(exc), code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        rendering.fail(str(exc), code=exit_codes.AMBIGUOUS, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), cause=exc)

    dispatch = result.route.dispatch
    skill = None if dispatch is None else entry_for(dispatch.variant, layout=layout).skill
    payload = rendering.next_payload(result, bundle_root=layout.bundle_dir, skill=skill)
    if json_output:
        rendering.emit(payload)
        for warning in result.warnings:
            rendering.warn(warning)
    else:
        rendering.render_next(result, payload)
    if payload["blockers"]:
        raise typer.Exit(code=exit_codes.GENERIC)


@work_app.command()
def advance(
    slug: str = typer.Argument(..., help="Work item slug (file stem under work/)."),
    effort: str = typer.Option("", "--effort", help="xtra-small|small|medium|large|xtra-large."),
    owner: str = typer.Option("", "--owner", help="Handle to record when execution starts."),
    resolved_in: str = typer.Option("", "--resolved-in", help="PR or commit ref."),
    worktree: str = typer.Option("", "--worktree", help="The item's worktree path, when the caller knows it."),
    branch: str = typer.Option("", "--branch", help="The item's branch, paired with --worktree."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the advance as JSON."),
) -> None:
    """Apply the routing table's next transition for SLUG.

    The pipeline's single mutation point. An explicit `--worktree`/`--branch`
    pair is applied unconditionally -- it is the caller's own resolved
    decision, which is what lets an item be evicted out of a shared main
    checkout; without the pair, provenance falls back to cwd inference.
    """
    warn_if_stale_routing()
    layout = resolve_workspace(workspace)
    try:
        result = run_stage_advance(
            layout,
            slug,
            today=_today(),
            effort=effort or None,
            owner=owner or None,
            resolved_in=resolved_in or None,
            worktree=worktree or None,
            branch=branch or None,
            dry_run=dry_run,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        rendering.fail(str(exc), code=exit_codes.AMBIGUOUS, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), cause=exc)

    payload = rendering.advance_payload(result, slug)
    if payload["refusal"] is not None:
        rendering.fail(f"{slug}: refused ({payload['refusal']['reason']}) — {payload['refusal']['detail']}")

    if json_output:
        rendering.emit(payload)
        if payload["repo_note"]:
            rendering.warn(payload["repo_note"])
        return
    if dry_run:
        typer.echo(result.outcome.plan.diff())
        return
    rendering.render_advance(payload)


@work_app.command()
def orchestrate(
    slug: str = typer.Argument(..., help="Root work item slug to plan dispatches for."),
    live: str = typer.Option("", "--live", help="Comma-separated running dispatch keys (<slug>#<phase>)."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the dispatch plan as JSON."),
) -> None:
    """Compute the auto-drive dispatch plan for SLUG's subtree. Read-only.

    A planner, not an executor: it never launches a dispatch, mutates an item,
    provisions a worktree, or edits the manifest.
    """
    warn_if_stale_routing()
    layout = resolve_workspace(workspace)
    try:
        result = run_orchestrate(layout, slug, live=tuple(rendering.split_csv(live)))
    except WorkspaceError as exc:
        rendering.fail(str(exc), code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        rendering.fail(str(exc), code=exit_codes.AMBIGUOUS, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), cause=exc)

    payload = rendering.orchestrate_payload(result)
    if json_output:
        rendering.emit(payload)
        for warning in payload["warnings"]:
            rendering.warn(warning)
    else:
        rendering.render_orchestrate(payload)


@work_app.command(name="regen-index")
def regen_index(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the reconciliation as JSON."),
) -> None:
    """Reconcile `work/index.md` against what is on disk.

    Reconciles the Markdown index; it does not rebuild a JSON sidecar. That
    migration is deliberate (spec 1) and `work-index.json` is not coming back.
    """
    layout = resolve_workspace(workspace)
    try:
        update = work.run_regen_index(layout, dry_run=dry_run)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), cause=exc)

    payload = rendering.regen_index_payload(update)
    if json_output:
        rendering.emit(payload)
    elif update.changed:
        typer.echo(f"{'would reconcile' if dry_run else 'reconciled'} {update.path}")
        typer.echo(update.diff())
    else:
        typer.echo("nothing to do")


@work_app.command()
def archive(
    slugs: list[str] = typer.Argument(None, help="Slugs to archive. Omit to sweep every eligible item."),  # noqa: B008
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of moving."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the archive run as JSON."),
) -> None:
    """Relocate terminal work items to `work/_archive/`, repairing referrers.

    Sweep mode reports no skips -- a sweep's non-candidates were never
    candidates. Targeted mode reports one per named slug that did not move,
    and exits non-zero, because there the caller named the slug and is owed
    an answer. The wiki lane is never involved.
    """
    layout = resolve_workspace(workspace)
    targeted = list(slugs or ())
    try:
        run = run_archive(layout, targeted or None, today=_today(), dry_run=dry_run)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), cause=exc)

    payload = rendering.archive_payload(run, dry_run=dry_run)
    if json_output:
        rendering.emit(payload)
    else:
        typer.echo(run.plan.diff() or "nothing to archive")
        for token in payload["archived"]:
            typer.echo(f"archived {token}")
        for relative in payload["pruned"]:
            typer.echo(f"pruned {relative}")
        for path in payload["indexes"]:
            typer.echo(f"reconciled {path}")
        if payload["logged"]:
            typer.echo(f"log.md: {payload['logged']}")
        for skip in payload["skipped"]:
            rendering.warn(f"skipped {skip['slug']} ({skip['reason']}): {skip['detail']}")

    if payload["conflict"]:
        rendering.fail(f"cross-lane conflict on {', '.join(payload['conflict'])}; nothing was applied")
    if not run.ok or (targeted and payload["skipped"]):
        raise typer.Exit(code=exit_codes.GENERIC)


@work_app.command(name="adopt-child-specs")
def adopt_child_specs(
    epic_slug: str = typer.Argument(..., help="Epic slug (file stem under work/)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of moving."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = typer.Option(False, "--json", help="Emit the adoption as JSON."),
) -> None:
    """Adopt pre-written child-spec drafts into EPIC_SLUG's filed children.

    Mechanical: a draft whose suffix identifies more than one direct child is
    reported as ambiguous and skipped, never guessed at.
    """
    layout = resolve_workspace(workspace)
    try:
        result = work.run_adopt_child_specs(layout, epic_slug, dry_run=dry_run)
    except ValueError as exc:
        rendering.fail(str(exc), code=exit_codes.AMBIGUOUS, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), cause=exc)

    payload = rendering.adopt_payload(result)
    if json_output:
        rendering.emit(payload)
    else:
        verb = "would adopt" if dry_run else "adopted"
        typer.echo(f"[ok] {payload['epic_slug']}: {verb} {len(payload['adopted'])}")
        for entry in payload["adopted"]:
            typer.echo(f"  {verb} {entry['child']} <- {entry['from']}")
        for child in payload["unseeded_children"]:
            typer.echo(f"  unseeded: {child}")
        for stem in payload["orphaned_drafts"]:
            rendering.warn(f"orphaned draft: {stem}")
        for entry in payload["ambiguous"]:
            rendering.warn(f"ambiguous {entry['stem']}: {', '.join(entry['candidates'])}")
        for warning in payload["warnings"]:
            rendering.warn(warning)

    if payload["refusal"] is not None:
        rendering.fail(f"{payload['epic_slug']}: refused ({payload['refusal']})")
